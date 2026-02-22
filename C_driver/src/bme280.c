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

    /* Read temperature calibration 0x88..0x8D (6 bytes) */
    {
        uint8_t calib_buf[6];
        if (hal_read_retry(dev, 0x88, calib_buf, sizeof(calib_buf)) < 0) {
            log_msg(dev, BME280_LOG_ERROR, BME280_E_I2C, "Failed to read calibration");
            return BME_ERR(dev, BME280_E_I2C);
        }

        dev->calib.dig_T1 = (uint16_t)((calib_buf[1] << 8) | calib_buf[0]);
        dev->calib.dig_T2 = (int16_t)((calib_buf[3] << 8) | calib_buf[2]);
        dev->calib.dig_T3 = (int16_t)((calib_buf[5] << 8) | calib_buf[4]);
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

bme280_err_t bme280_read_temperature(bme280_t *dev, float *temp_c)
{
    if (!dev || !temp_c) return BME_ERR(dev, BME280_E_INVALID_ARG);
    if (!dev->initialized) return BME_ERR(dev, BME280_E_NOT_INITIALIZED);

    dev->stats.reads_total++;

    uint64_t t0 = now_us(dev);

    /* Trigger forced measurement (0x25) */
    {
        const uint8_t v = 0x25;
        if (hal_write_retry(dev, 0xF4, &v, 1) < 0) {
            dev->stats.reads_fail++;
            log_msg(dev, BME280_LOG_ERROR, BME280_E_I2C, "Failed to trigger measurement");
            return BME_ERR(dev, BME280_E_I2C);
        }
    }

    /* Wait until measurement completes: status bit 3 (0x08) clears */
    uint8_t status = 0;
    uint32_t iters = max_poll_iters(&dev->cfg);
    for (uint32_t i = 0; i < iters; i++) {
        if (hal_read_retry(dev, 0xF3, &status, 1) < 0) {
            dev->stats.reads_fail++;
            log_msg(dev, BME280_LOG_ERROR, BME280_E_I2C, "Failed to read status");
            return BME_ERR(dev, BME280_E_I2C);
        }
        if ((status & 0x08) == 0) {
            break;
        }
        dev->hal.sleep_us(dev->hal.ctx, dev->cfg.poll_interval_us);
        if (i == iters - 1) {
            dev->stats.timeouts++;
            dev->stats.reads_fail++;
            log_msg(dev, BME280_LOG_WARN, BME280_E_TIMEOUT, "Measurement timeout");
            if (dev->cfg.reset_on_error) (void)bme280_reset(dev);
            return BME_ERR(dev, BME280_E_TIMEOUT);
        }
    }

    /* Read raw temperature bytes: 0xFA..0xFC (3 bytes) */
    uint8_t buf[3];
    if (hal_read_retry(dev, 0xFA, buf, sizeof(buf)) < 0) {
        dev->stats.reads_fail++;
        log_msg(dev, BME280_LOG_ERROR, BME280_E_I2C, "Failed to read temperature raw data");
        return BME_ERR(dev, BME280_E_I2C);
    }

    int32_t adc_T = (int32_t)((buf[0] << 12) | (buf[1] << 4) | (buf[2] >> 4));

    /* Compensation (same as your existing code) */
    int32_t var1 = ((((adc_T >> 3) - ((int32_t)dev->calib.dig_T1 << 1))) *
                    ((int32_t)dev->calib.dig_T2)) >> 11;

    int32_t var2 = (((((adc_T >> 4) - ((int32_t)dev->calib.dig_T1)) *
                      ((adc_T >> 4) - ((int32_t)dev->calib.dig_T1))) >> 12) *
                    ((int32_t)dev->calib.dig_T3)) >> 14;

    dev->t_fine = var1 + var2;
    int32_t T = (dev->t_fine * 5 + 128) >> 8;
    *temp_c = T / 100.0f;

    /* Update latency stats if time source exists */
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