#include "bme280.h"

#include <string.h> /* memcpy */

/* -------- internal helpers -------- */

static void log_msg(bme280_t *dev, bme280_log_level_t lvl, bme280_err_t code, const char *msg)
{
    if (!dev || !dev->log_fn) return;
    if (lvl > dev->cfg.log_level) return;
    dev->log_fn(dev->log_ctx, lvl, code, msg);
}

static bme280_err_t set_err(bme280_t *dev, bme280_err_t e, uint32_t line)
{
    if (dev) {
        dev->stats.last_error = e;
        dev->stats.last_error_line = line;
    }
    return e;
}

#define BME_ERR(dev, e) set_err((dev), (e), (uint32_t)__LINE__)

/* -------- forward declarations -------- */
static int hal_read_retry(bme280_t *dev, uint8_t reg, uint8_t *buf, size_t len);
static int hal_write_retry(bme280_t *dev, uint8_t reg, const uint8_t *buf, size_t len);
static uint32_t max_poll_iters(const bme280_config_t *cfg);
static float compensate_T(bme280_t *dev, int32_t adc_T);
static float compensate_P(bme280_t *dev, int32_t adc_P);
static float compensate_H(bme280_t *dev, int32_t adc_H);

typedef struct {
    int32_t adc_T;
    int32_t adc_P;
    int32_t adc_H;
} bme280_raw_t;

/* Read a full raw data burst: 0xF7..0xFE (8 bytes) */
static bme280_err_t bme280_measure_and_read_raw(bme280_t *dev, bme280_raw_t *raw)
{
    if (!dev || !raw) return BME_ERR(dev, BME280_E_INVALID_ARG);

    /* Trigger forced measurement (same as before) */
    const uint8_t ctrl_meas = 0x25; /* osrs_t=1, osrs_p=1, mode=forced */
    if (hal_write_retry(dev, 0xF4, &ctrl_meas, 1) < 0) {
        log_msg(dev, BME280_LOG_ERROR, BME280_E_I2C, "Failed to trigger measurement");
        return BME_ERR(dev, BME280_E_I2C);
    }

    /* Wait for measurement done (status bit 3 clears) */
    uint8_t status = 0;
    uint32_t iters = max_poll_iters(&dev->cfg);
    for (uint32_t i = 0; i < iters; i++) {
        if (hal_read_retry(dev, 0xF3, &status, 1) < 0) {
            log_msg(dev, BME280_LOG_ERROR, BME280_E_I2C, "Failed to read status");
            return BME_ERR(dev, BME280_E_I2C);
        }
        if ((status & 0x08) == 0) break;

        dev->hal.sleep_us(dev->hal.ctx, dev->cfg.poll_interval_us);
        if (i == iters - 1) {
            dev->stats.timeouts++;
            log_msg(dev, BME280_LOG_WARN, BME280_E_TIMEOUT, "Measurement timeout");
            if (dev->cfg.reset_on_error) (void)bme280_reset(dev);
            return BME_ERR(dev, BME280_E_TIMEOUT);
        }
    }

    /* Burst read raw data: 0xF7..0xFE */
    uint8_t b[8];
    if (hal_read_retry(dev, 0xF7, b, sizeof(b)) < 0) {
        log_msg(dev, BME280_LOG_ERROR, BME280_E_I2C, "Failed to read raw block");
        return BME_ERR(dev, BME280_E_I2C);
    }

    /* Parse raw values */
    raw->adc_P = (int32_t)((b[0] << 12) | (b[1] << 4) | (b[2] >> 4));
    raw->adc_T = (int32_t)((b[3] << 12) | (b[4] << 4) | (b[5] >> 4));
    raw->adc_H = (int32_t)((b[6] << 8)  | (b[7]));

    return BME280_OK;
}

static int hal_set_addr(bme280_t *dev)
{
    if (dev->hal.set_addr) {
        return dev->hal.set_addr(dev->hal.ctx, dev->addr);
    }
    return 0;
}

/* Retry wrapper: returns 0 on success, -1 on failure */
static int hal_read_retry(bme280_t *dev, uint8_t reg, uint8_t *buf, size_t len)
{
    uint32_t attempts = dev->cfg.max_retries + 1;
    for (uint32_t i = 0; i < attempts; i++) {
        if (hal_set_addr(dev) < 0) {
            dev->stats.i2c_errors++;
        } else if (dev->hal.read(dev->hal.ctx, reg, buf, len) == 0) {
            if (i > 0) dev->stats.retries += i;
            return 0;
        } else {
            dev->stats.i2c_errors++;
        }
    }
    return -1;
}

static int hal_write_retry(bme280_t *dev, uint8_t reg, const uint8_t *buf, size_t len)
{
    uint32_t attempts = dev->cfg.max_retries + 1;
    for (uint32_t i = 0; i < attempts; i++) {
        if (hal_set_addr(dev) < 0) {
            dev->stats.i2c_errors++;
        } else if (dev->hal.write(dev->hal.ctx, reg, buf, len) == 0) {
            if (i > 0) dev->stats.retries += i;
            return 0;
        } else {
            dev->stats.i2c_errors++;
        }
    }
    return -1;
}

static uint64_t now_us(bme280_t *dev)
{
    if (dev->hal.time_now_us) return dev->hal.time_now_us(dev->hal.ctx);
    return 0;
}

/* Compute max polling iterations from timeout_ms + poll_interval_us */
static uint32_t max_poll_iters(const bme280_config_t *cfg)
{
    if (!cfg || cfg->poll_interval_us == 0) return 1;
    uint64_t total_us = (uint64_t)cfg->timeout_ms * 1000ULL;
    uint64_t iters = total_us / (uint64_t)cfg->poll_interval_us;
    if (iters == 0) iters = 1;
    if (iters > 1000000ULL) iters = 1000000ULL; /* sanity cap */
    return (uint32_t)iters;
}

/* -------- public API -------- */

bme280_err_t bme280_init(
    bme280_t *dev,
    const bme280_hal_t *hal,
    uint8_t addr,
    const bme280_config_t *cfg,
    bme280_log_fn log_fn,
    void *log_ctx
)
{
    if (!dev || !hal || !hal->read || !hal->write || !hal->sleep_us) {
        return BME280_E_INVALID_ARG;
    }

    memset(dev, 0, sizeof(*dev));
    dev->addr = addr;
    dev->hal = *hal;
    dev->cfg = cfg ? *cfg : bme280_default_config();
    dev->log_fn = log_fn;
    dev->log_ctx = log_ctx;

    bme280_stats_reset(&dev->stats);

    /* Read chip ID */
    uint8_t id = 0;
    if (hal_read_retry(dev, 0xD0, &id, 1) < 0) {
        log_msg(dev, BME280_LOG_ERROR, BME280_E_I2C, "Failed to read chip ID");
        return BME_ERR(dev, BME280_E_I2C);
    }

    if (id != 0x60) {
        log_msg(dev, BME280_LOG_ERROR, BME280_E_BAD_CHIP_ID, "Unexpected chip ID");
        return BME_ERR(dev, BME280_E_BAD_CHIP_ID);
    }

    /* Soft reset */
    {
        const uint8_t v = 0xB6;
        if (hal_write_retry(dev, 0xE0, &v, 1) < 0) {
            log_msg(dev, BME280_LOG_ERROR, BME280_E_I2C, "Failed to soft reset");
            return BME_ERR(dev, BME280_E_I2C);
        }
        dev->hal.sleep_us(dev->hal.ctx, 2000); /* 2ms reset time */
        dev->stats.resets++;
    }

    /* Read calibration:
       - 0x88..0xA1 (26 bytes): T1..T3, P1..P9, H1
       - 0xE1..0xE7 (7 bytes):  H2..H6 (and packed H4/H5)
    */
    {
        uint8_t c1[26];
        uint8_t c2[7];

        if (hal_read_retry(dev, 0x88, c1, sizeof(c1)) < 0) {
            log_msg(dev, BME280_LOG_ERROR, BME280_E_I2C, "Failed to read calib block 1");
            return BME_ERR(dev, BME280_E_I2C);
        }

        if (hal_read_retry(dev, 0xE1, c2, sizeof(c2)) < 0) {
            log_msg(dev, BME280_LOG_ERROR, BME280_E_I2C, "Failed to read calib block 2");
            return BME_ERR(dev, BME280_E_I2C);
        }

        /* Temperature */
        dev->calib.dig_T1 = (uint16_t)((c1[1] << 8) | c1[0]);
        dev->calib.dig_T2 = (int16_t)((c1[3] << 8) | c1[2]);
        dev->calib.dig_T3 = (int16_t)((c1[5] << 8) | c1[4]);

        /* Pressure */
        dev->calib.dig_P1 = (uint16_t)((c1[7]  << 8) | c1[6]);
        dev->calib.dig_P2 = (int16_t)((c1[9]  << 8) | c1[8]);
        dev->calib.dig_P3 = (int16_t)((c1[11] << 8) | c1[10]);
        dev->calib.dig_P4 = (int16_t)((c1[13] << 8) | c1[12]);
        dev->calib.dig_P5 = (int16_t)((c1[15] << 8) | c1[14]);
        dev->calib.dig_P6 = (int16_t)((c1[17] << 8) | c1[16]);
        dev->calib.dig_P7 = (int16_t)((c1[19] << 8) | c1[18]);
        dev->calib.dig_P8 = (int16_t)((c1[21] << 8) | c1[20]);
        dev->calib.dig_P9 = (int16_t)((c1[23] << 8) | c1[22]);

        /* Humidity */
        dev->calib.dig_H1 = c1[25];
        dev->calib.dig_H2 = (int16_t)((c2[1] << 8) | c2[0]);
        dev->calib.dig_H3 = c2[2];

        /* H4 and H5 are packed across bytes (datasheet format) */
        dev->calib.dig_H4 = (int16_t)((c2[3] << 4) | (c2[4] & 0x0F));
        dev->calib.dig_H5 = (int16_t)((c2[5] << 4) | (c2[4] >> 4));

        dev->calib.dig_H6 = (int8_t)c2[6];
    }

    /* Humidity oversampling x1 */
    {
        const uint8_t v = 0x01;
        if (hal_write_retry(dev, 0xF2, &v, 1) < 0) {
            log_msg(dev, BME280_LOG_ERROR, BME280_E_I2C, "Failed to set humidity oversampling");
            return BME_ERR(dev, BME280_E_I2C);
        }
    }

    /* Temp + pressure oversampling x1, forced mode */
    {
        const uint8_t v = 0x25;
        if (hal_write_retry(dev, 0xF4, &v, 1) < 0) {
            log_msg(dev, BME280_LOG_ERROR, BME280_E_I2C, "Failed to set ctrl_meas");
            return BME_ERR(dev, BME280_E_I2C);
        }
    }

    dev->initialized = 1;
    log_msg(dev, BME280_LOG_INFO, BME280_OK, "BME280 initialized");
    return BME280_OK;
}

bme280_err_t bme280_reset(bme280_t *dev)
{
    if (!dev || !dev->initialized) return BME_ERR(dev, BME280_E_NOT_INITIALIZED);

    const uint8_t v = 0xB6;
    if (hal_write_retry(dev, 0xE0, &v, 1) < 0) {
        log_msg(dev, BME280_LOG_ERROR, BME280_E_I2C, "Reset failed");
        return BME_ERR(dev, BME280_E_I2C);
    }
    dev->hal.sleep_us(dev->hal.ctx, 2000);
    dev->stats.resets++;
    return BME280_OK;
}

bme280_err_t bme280_read(bme280_t *dev, bme280_read_mask_t mask, bme280_reading_t *out)
{
    if (!dev || !out) return BME_ERR(dev, BME280_E_INVALID_ARG);
    if (!dev->initialized) return BME_ERR(dev, BME280_E_NOT_INITIALIZED);
    if ((mask & BME280_READ_ALL) == 0) return BME_ERR(dev, BME280_E_INVALID_ARG);

    dev->stats.reads_total++;
    uint64_t t0 = now_us(dev);

    bme280_raw_t raw;
    bme280_err_t e = bme280_measure_and_read_raw(dev, &raw);
    if (e != BME280_OK) {
        dev->stats.reads_fail++;
        return e;
    }

    /* Always compute temperature first if we need P or H because it sets t_fine */
    out->valid = mask;
    if (mask & (BME280_READ_TEMP | BME280_READ_PRESS | BME280_READ_HUM)) {
        out->temperature_c = compensate_T(dev, raw.adc_T);
    }

    if (mask & BME280_READ_PRESS) {
        out->pressure_pa = compensate_P(dev, raw.adc_P);
    }

    if (mask & BME280_READ_HUM) {
        out->humidity_rh = compensate_H(dev, raw.adc_H);
    }

    /* latency stats */
    uint64_t t1 = now_us(dev);
    if (t0 && t1 && t1 >= t0) {
        uint64_t dt = t1 - t0;
        dev->stats.last_latency_us = dt;
        dev->stats.latency_sum_us += dt;
        if (dt > dev->stats.latency_max_us) dev->stats.latency_max_us = dt;
    } else {
        dev->stats.last_latency_us = 0;
    }

    dev->stats.reads_ok++;
    dev->stats.last_error = BME280_OK;
    dev->stats.last_error_line = 0;
    return BME280_OK;
}

void bme280_get_stats(const bme280_t *dev, bme280_stats_t *out)
{
    if (!dev || !out) return;
    *out = dev->stats;
}

static float compensate_T(bme280_t *dev, int32_t adc_T)
{
    int32_t var1 = ((((adc_T >> 3) - ((int32_t)dev->calib.dig_T1 << 1))) *
                    ((int32_t)dev->calib.dig_T2)) >> 11;

    int32_t var2 = (((((adc_T >> 4) - ((int32_t)dev->calib.dig_T1)) *
                      ((adc_T >> 4) - ((int32_t)dev->calib.dig_T1))) >> 12) *
                    ((int32_t)dev->calib.dig_T3)) >> 14;

    dev->t_fine = var1 + var2;
    int32_t T = (dev->t_fine * 5 + 128) >> 8;
    return (float)T / 100.0f;
}

/* Returns pressure in Pa */
static float compensate_P(bme280_t *dev, int32_t adc_P)
{
    int64_t var1 = (int64_t)dev->t_fine - 128000;
    int64_t var2 = var1 * var1 * (int64_t)dev->calib.dig_P6;
    var2 = var2 + ((var1 * (int64_t)dev->calib.dig_P5) << 17);
    var2 = var2 + (((int64_t)dev->calib.dig_P4) << 35);
    var1 = ((var1 * var1 * (int64_t)dev->calib.dig_P3) >> 8) + ((var1 * (int64_t)dev->calib.dig_P2) << 12);
    var1 = (((((int64_t)1) << 47) + var1) * (int64_t)dev->calib.dig_P1) >> 33;

    if (var1 == 0) return 0.0f; /* avoid division by zero */

    int64_t p = 1048576 - adc_P;
    p = (((p << 31) - var2) * 3125) / var1;
    var1 = ((int64_t)dev->calib.dig_P9 * (p >> 13) * (p >> 13)) >> 25;
    var2 = ((int64_t)dev->calib.dig_P8 * p) >> 19;
    p = ((p + var1 + var2) >> 8) + (((int64_t)dev->calib.dig_P7) << 4);

    /* p is Q24.8 Pa */
    return (float)p / 256.0f;
}

/* Returns humidity in %RH */
static float compensate_H(bme280_t *dev, int32_t adc_H)
{
    int32_t v_x1 = dev->t_fine - 76800;

    v_x1 = (((((adc_H << 14) - ((int32_t)dev->calib.dig_H4 << 20) -
               ((int32_t)dev->calib.dig_H5 * v_x1)) + 16384) >> 15) *
            (((((((v_x1 * (int32_t)dev->calib.dig_H6) >> 10) *
                 (((v_x1 * (int32_t)dev->calib.dig_H3) >> 11) + 32768)) >> 10) + 2097152) *
               (int32_t)dev->calib.dig_H2 + 8192) >> 14));

    v_x1 = v_x1 - (((((v_x1 >> 15) * (v_x1 >> 15)) >> 7) * (int32_t)dev->calib.dig_H1) >> 4);

    if (v_x1 < 0) v_x1 = 0;
    if (v_x1 > 419430400) v_x1 = 419430400;

    uint32_t h = (uint32_t)(v_x1 >> 12); /* Q20.12 */
    return (float)h / 1024.0f;
}