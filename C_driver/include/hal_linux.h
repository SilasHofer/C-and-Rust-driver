#pragma once
#ifndef HAL_LINUX_H
#define HAL_LINUX_H

#include <stdint.h>
#include "bme280_hal.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Context for Linux I2C HAL */
typedef struct {
    int fd;
    uint8_t current_addr;
} bme280_linux_ctx_t;

/* Initialize a Linux HAL for a given fd.
   - You still open("/dev/i2c-1") in your app/harness
   - The driver will call set_addr before each transaction (safe + simple)
*/
void bme280_linux_hal_make(bme280_hal_t *out_hal, bme280_linux_ctx_t *out_ctx, int fd);

#ifdef __cplusplus
}
#endif

#endif /* HAL_LINUX_H */