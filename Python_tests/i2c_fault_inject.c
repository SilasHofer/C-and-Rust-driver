/*
 * i2c_fault_inject.c
 * LD_PRELOAD fault injector for user-space I2C drivers (BME280 C & Rust).
 *
 * Intercepts:
 *   - ioctl(fd, I2C_RDWR, ...)  → read path (i2c_read_u8, i2c_read_buf)
 *   - write(fd, buf, len)       → write path (i2c_write_u8)
 *
 * Environment variables:
 *   FAULT_TARGET   read | write | both   (default: both)
 *   FAULT_MODE     every_n | prob        (default: every_n)
 *   FAULT_EVERY_N  integer               (default: 5)
 *   FAULT_PROB     0.0–1.0               (default: 0.1)
 *   FAULT_ERRNO    errno value           (default: 5 = EIO)
 *
 * Build:
 *   gcc -shared -fPIC -o i2c_fault_inject.so i2c_fault_inject.c -ldl
 *
 * Usage:
 *   FAULT_TARGET=read FAULT_EVERY_N=5 \
 *     LD_PRELOAD=./i2c_fault_inject.so ./c_driver 0x76 0
 */

#define _GNU_SOURCE
#include <dlfcn.h>
#include <linux/i2c.h>
#include <linux/i2c-dev.h>
#include <sys/ioctl.h>
#include <stdarg.h>
#include <stdio.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

/* ── Real syscall pointers ─────────────────────────────────────────────── */
static int    (*real_ioctl)(int, unsigned long, ...) = NULL;
static ssize_t(*real_write)(int, const void *, size_t) = NULL;

/* ── Config ────────────────────────────────────────────────────────────── */
typedef enum { TARGET_READ, TARGET_WRITE, TARGET_BOTH } FaultTarget;
typedef enum { MODE_EVERY_N, MODE_PROB              } FaultMode;

static FaultTarget fault_target  = TARGET_BOTH;
static FaultMode   fault_mode    = MODE_EVERY_N;
static unsigned    fail_every_n  = 5;
static double      fail_prob     = 0.1;
static int         fault_errno   = EIO;

static unsigned    read_count    = 0;
static unsigned    write_count   = 0;

/* ── Init ──────────────────────────────────────────────────────────────── */
static void __attribute__((constructor)) fi_init(void)
{
    real_ioctl = dlsym(RTLD_NEXT, "ioctl");
    real_write = dlsym(RTLD_NEXT, "write");

    const char *target = getenv("FAULT_TARGET");
    const char *mode   = getenv("FAULT_MODE");
    const char *every  = getenv("FAULT_EVERY_N");
    const char *prob   = getenv("FAULT_PROB");
    const char *ferr   = getenv("FAULT_ERRNO");

    if (target) {
        if      (strcmp(target, "read")  == 0) fault_target = TARGET_READ;
        else if (strcmp(target, "write") == 0) fault_target = TARGET_WRITE;
        else                                   fault_target = TARGET_BOTH;
    }
    if (mode && strcmp(mode, "prob") == 0) fault_mode = MODE_PROB;
    if (every)  fail_every_n = (unsigned)atoi(every);
    if (prob)   fail_prob    = atof(prob);
    if (ferr)   fault_errno  = atoi(ferr);

    srand((unsigned)time(NULL));

    fprintf(stderr,
        "[i2c_fault_inject] target=%s mode=%s every_n=%u prob=%.2f errno=%d\n",
        target  ? target : "both",
        mode    ? mode   : "every_n",
        fail_every_n, fail_prob, fault_errno);
}

/* ── Failure decision ──────────────────────────────────────────────────── */
static int should_fail(unsigned call_n)
{
    if (fault_mode == MODE_EVERY_N)
        return (fail_every_n > 0 && call_n % fail_every_n == 0);
    else
        return ((double)rand() / RAND_MAX) < fail_prob;
}

/* ── ioctl interception (read path) ────────────────────────────────────── */
int ioctl(int fd, unsigned long request, ...)
{
    va_list args;
    va_start(args, request);
    void *arg = va_arg(args, void *);
    va_end(args);

    if (request == I2C_RDWR &&
        (fault_target == TARGET_READ || fault_target == TARGET_BOTH))
    {
        read_count++;
        if (should_fail(read_count)) {
            fprintf(stderr,
                "[i2c_fault_inject] READ fault #%u → errno=%d (EIO=%d ENODEV=%d ETIMEDOUT=%d)\n",
                read_count, fault_errno, EIO, ENODEV, ETIMEDOUT);
            errno = fault_errno;
            return -1;
        }
    }

    return real_ioctl(fd, request, arg);
}

/* ── write interception (write path) ───────────────────────────────────── */
ssize_t write(int fd, const void *buf, size_t count)
{
    /*
     * Only intercept 2-byte payloads on an I2C fd.
     * i2c_write_u8 always sends exactly [reg, data] = 2 bytes.
     * Passing through all other writes (stdout, file descriptors, etc.)
     * avoids corrupting unrelated I/O.
     */
    if (count == 2 &&
        (fault_target == TARGET_WRITE || fault_target == TARGET_BOTH))
    {
        write_count++;
        if (should_fail(write_count)) {
            fprintf(stderr,
                "[i2c_fault_inject] WRITE fault #%u → errno=%d\n",
                write_count, fault_errno);
            errno = fault_errno;
            return -1;
        }
    }

    return real_write(fd, buf, count);
}
