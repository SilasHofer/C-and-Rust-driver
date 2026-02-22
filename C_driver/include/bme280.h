#pragma once
#ifndef BME280_H
#define BME280_H

#include <stdint.h>
#include "bme280_types.h"
#include "bme280_hal.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    /* Temperature */
    uint16_t dig_T1;
    int16_t  dig_T2;
    int16_t  dig_T3;

    /* Pressure */
    uint16_t dig_P1;
    int16_t  dig_P2;
    int16_t  dig_P3;
    int16_t  dig_P4;
    int16_t  dig_P5;
    int16_t  dig_P6;
    int16_t  dig_P7;
    int16_t  dig_P8;
    int16_t  dig_P9;

    /* Humidity */
    uint8_t  dig_H1;
    int16_t  dig_H2;
    uint8_t  dig_H3;
    int16_t  dig_H4;
    int16_t  dig_H5;
    int8_t   dig_H6;
} bme280_calib_t;

typedef struct bme280 {
    /* Sensor identity/config */
    uint8_t addr;

    /* Dependencies */
    bme280_hal_t hal;

    /* Policy & instrumentation */
    bme280_config_t cfg;
    bme280_stats_t  stats;

    /* Logging */
    bme280_log_fn log_fn;
    void *log_ctx;

    /* Calibration + internal state */
    bme280_calib_t calib;
    int32_t t_fine;

    /* Init flag */
    uint8_t initialized;
} bme280_t;

/* Public API */
bme280_err_t bme280_init(
    bme280_t *dev,
    const bme280_hal_t *hal,
    uint8_t addr,
    const bme280_config_t *cfg,
    bme280_log_fn log_fn,
    void *log_ctx
);

bme280_err_t bme280_reset(bme280_t *dev);

/* What to read */
typedef enum {
    BME280_READ_TEMP  = 1u << 0,
    BME280_READ_PRESS = 1u << 1,
    BME280_READ_HUM   = 1u << 2,
    BME280_READ_ALL   = BME280_READ_TEMP | BME280_READ_PRESS | BME280_READ_HUM
} bme280_read_mask_t;

typedef struct {
    /* Which fields are valid (echo of requested mask) */
    bme280_read_mask_t valid;

    float temperature_c;
    float pressure_pa;
    float humidity_rh;
} bme280_reading_t;

/* New unified API */
bme280_err_t bme280_read(bme280_t *dev, bme280_read_mask_t mask, bme280_reading_t *out);

/* Keep convenience wrappers if you want */
static inline bme280_err_t bme280_read_temperature(bme280_t *dev, float *temp_c)
{
    bme280_reading_t r;
    bme280_err_t e = bme280_read(dev, BME280_READ_TEMP, &r);
    if (e == BME280_OK && temp_c) *temp_c = r.temperature_c;
    return e;
}

static inline bme280_err_t bme280_read_pressure(bme280_t *dev, float *press_pa)
{
    bme280_reading_t r;
    bme280_err_t e = bme280_read(dev, BME280_READ_PRESS, &r);
    if (e == BME280_OK && press_pa) *press_pa = r.pressure_pa;
    return e;
}

static inline bme280_err_t bme280_read_humidity(bme280_t *dev, float *hum_rh)
{
    bme280_reading_t r;
    bme280_err_t e = bme280_read(dev, BME280_READ_HUM, &r);
    if (e == BME280_OK && hum_rh) *hum_rh = r.humidity_rh;
    return e;
}

void bme280_get_stats(const bme280_t *dev, bme280_stats_t *out);

#ifdef __cplusplus
}
#endif

#endif /* BME280_H */