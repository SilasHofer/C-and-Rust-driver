#pragma once
#ifndef BME280_TYPES_H
#define BME280_TYPES_H

#include <stdint.h>
#include <stddef.h>
#include <inttypes.h>

#ifdef __cplusplus
extern "C" {
#endif

/* -----------------------------
 * Error handling
 * ----------------------------- */

typedef enum {
    BME280_OK = 0,

    /* Caller / API errors */
    BME280_E_INVALID_ARG = 1,
    BME280_E_NOT_INITIALIZED,
    BME280_E_UNSUPPORTED,

    /* Hardware / bus errors */
    BME280_E_I2C = 10,
    BME280_E_NACK,
    BME280_E_IO,

    /* Sensor / protocol errors */
    BME280_E_BAD_CHIP_ID = 20,
    BME280_E_TIMEOUT,
    BME280_E_CRC,          /* if you add CRC checks later */
    BME280_E_PARSE,
    BME280_E_RANGE,

    /* Internal errors */
    BME280_E_INTERNAL = 30
} bme280_err_t;

/* Convert error code to a short string (optional, very useful for logging) */
static inline const char *bme280_err_str(bme280_err_t e)
{
    switch (e) {
    case BME280_OK: return "OK";
    case BME280_E_INVALID_ARG: return "INVALID_ARG";
    case BME280_E_NOT_INITIALIZED: return "NOT_INITIALIZED";
    case BME280_E_UNSUPPORTED: return "UNSUPPORTED";
    case BME280_E_I2C: return "I2C_ERROR";
    case BME280_E_NACK: return "I2C_NACK";
    case BME280_E_IO: return "IO_ERROR";
    case BME280_E_BAD_CHIP_ID: return "BAD_CHIP_ID";
    case BME280_E_TIMEOUT: return "TIMEOUT";
    case BME280_E_CRC: return "CRC_ERROR";
    case BME280_E_PARSE: return "PARSE_ERROR";
    case BME280_E_RANGE: return "OUT_OF_RANGE";
    case BME280_E_INTERNAL: return "INTERNAL_ERROR";
    default: return "UNKNOWN";
    }
}

/* -----------------------------
 * Logging
 * ----------------------------- */

typedef enum {
    BME280_LOG_ERROR = 0,
    BME280_LOG_WARN  = 1,
    BME280_LOG_INFO  = 2,
    BME280_LOG_DEBUG = 3
} bme280_log_level_t;

/* Simple log callback. Keep formatting in the driver minimal.
   You can log structured info in the harness if you prefer. */
typedef void (*bme280_log_fn)(
    void *ctx,
    bme280_log_level_t level,
    bme280_err_t code,
    const char *msg
);

/* -----------------------------
 * Configuration
 * ----------------------------- */

typedef struct {
    /* Driver behaviour policy */
    uint32_t timeout_ms;        /* Max time to wait for measurement */
    uint32_t poll_interval_us;  /* Sleep between status polls */
    uint32_t max_retries;       /* Retry count for transient I/O issues */

    /* Optional behaviour */
    uint8_t  reset_on_error;    /* 0/1: issue soft reset after severe errors */
    uint8_t  reserved0;

    /* Logging */
    bme280_log_level_t log_level;
} bme280_config_t;

/* Provide a sane default config */
static inline bme280_config_t bme280_default_config(void)
{
    bme280_config_t cfg;
    cfg.timeout_ms = 50;            /* typical forced measurement budget */
    cfg.poll_interval_us = 2000;    /* your current 2ms */
    cfg.max_retries = 0;            /* start with none for fairness */
    cfg.reset_on_error = 0;
    cfg.reserved0 = 0;
    cfg.log_level = BME280_LOG_WARN;
    return cfg;
}

/* -----------------------------
 * Statistics (for metrics)
 * ----------------------------- */

typedef struct {
    uint64_t reads_total;
    uint64_t reads_ok;
    uint64_t reads_fail;

    uint64_t i2c_errors;
    uint64_t timeouts;
    uint64_t retries;
    uint64_t resets;

    /* Latency tracking (measured using HAL time source if available) */
    uint64_t last_latency_us;
    uint64_t latency_sum_us;
    uint64_t latency_max_us;

    /* Latest error info */
    bme280_err_t last_error;
    uint32_t     last_error_line;   /* optional: store __LINE__ on failure */
} bme280_stats_t;

/* Initialize stats to known values */
static inline void bme280_stats_reset(bme280_stats_t *s)
{
    if (!s) return;
    *s = (bme280_stats_t){0};
    s->last_error = BME280_OK;
    s->last_error_line = 0;
}

#ifdef __cplusplus
}
#endif

#endif /* BME280_TYPES_H */