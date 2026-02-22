#pragma once
#ifndef BME280_HAL_H
#define BME280_HAL_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    void *ctx;

    /* Optional: set the current slave address (useful for Linux /dev/i2c-*). */
    int (*set_addr)(void *ctx, uint8_t addr);

    /* Register-based operations */
    int (*read)(void *ctx, uint8_t reg, uint8_t *buf, size_t len);
    int (*write)(void *ctx, uint8_t reg, const uint8_t *buf, size_t len);

    /* Timing */
    void     (*sleep_us)(void *ctx, uint32_t us);
    uint64_t (*time_now_us)(void *ctx); /* optional but recommended */

} bme280_hal_t;

#ifdef __cplusplus
}
#endif

#endif /* BME280_HAL_H */