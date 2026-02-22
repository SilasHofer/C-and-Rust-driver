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
    uint16_t dig_T1;
    int16_t  dig_T2;
    int16_t  dig_T3;
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

bme280_err_t bme280_read_temperature(bme280_t *dev, float *temp_c);

void bme280_get_stats(const bme280_t *dev, bme280_stats_t *out);

#ifdef __cplusplus
}
#endif

#endif /* BME280_H */