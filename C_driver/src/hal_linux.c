#include "hal_linux.h"
#include "i2c_linux.h"

#include <unistd.h>
#include <time.h>
#include <string.h>

/* --- timing --- */
static uint64_t linux_time_now_us(void *ctx)
{
    (void)ctx;
    struct timespec ts;
    /* Monotonic clock is best for latency measurement */
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) return 0;

    uint64_t sec_us = (uint64_t)ts.tv_sec * 1000000ULL;
    uint64_t nsec_us = (uint64_t)ts.tv_nsec / 1000ULL;
    return sec_us + nsec_us;
}

static void linux_sleep_us(void *ctx, uint32_t us)
{
    (void)ctx;
    /* usleep is fine for userspace experiments */
    usleep(us);
}

/* --- address selection --- */
static int linux_set_addr(void *ctx, uint8_t addr)
{
    bme280_linux_ctx_t *c = (bme280_linux_ctx_t *)ctx;
    if (!c) return -1;

    /* Avoid redundant ioctl calls if address unchanged */
    if (c->current_addr == addr) return 0;

    if (i2c_set_slave(c->fd, addr) < 0) return -1;
    c->current_addr = addr;
    return 0;
}

/* --- register operations --- */
static int linux_read_reg(void *ctx, uint8_t reg, uint8_t *buf, size_t len)
{
    bme280_linux_ctx_t *c = (bme280_linux_ctx_t *)ctx;
    if (!c || !buf || len == 0) return -1;

    /* Your i2c_linux.c already does write(reg) then read(buf,len) */
    if (i2c_read_buf(c->fd, reg, buf, len) < 0) return -1;
    return 0;
}

static int linux_write_reg(void *ctx, uint8_t reg, const uint8_t *buf, size_t len)
{
    bme280_linux_ctx_t *c = (bme280_linux_ctx_t *)ctx;
    if (!c || !buf || len == 0) return -1;

    /* Your existing helpers only include i2c_write_u8, so implement a
       generic reg write by writing (reg + payload) directly to fd. */
    uint8_t tmp[1 + 32];

    if (len > 32) {
        /* keep it simple; extend if you ever need bigger writes */
        return -1;
    }

    tmp[0] = reg;
    memcpy(&tmp[1], buf, len);

    ssize_t want = (ssize_t)(1 + len);
    if (write(c->fd, tmp, (size_t)want) != want) return -1;
    return 0;
}

/* Public constructor */
void bme280_linux_hal_make(bme280_hal_t *out_hal, bme280_linux_ctx_t *out_ctx, int fd)
{
    if (!out_hal || !out_ctx) return;

    out_ctx->fd = fd;
    out_ctx->current_addr = 0; /* unknown initial */

    out_hal->ctx = out_ctx;
    out_hal->set_addr = linux_set_addr;
    out_hal->read = linux_read_reg;
    out_hal->write = linux_write_reg;
    out_hal->sleep_us = linux_sleep_us;
    out_hal->time_now_us = linux_time_now_us;
}