#include <stdio.h>
#include <stdint.h>
#include <fcntl.h>
#include <unistd.h>

#include "bme280.h"
#include "hal_linux.h"

static void my_log(void *ctx, bme280_log_level_t lvl, bme280_err_t code, const char *msg)
{
    (void)ctx;
    fprintf(stderr, "[%d] %s (%s)\n", (int)lvl, msg, bme280_err_str(code));
}

int main(int argc, char **argv)
{
    const char *i2c_path = "/dev/i2c-1";
    uint8_t addr = 0x76;

    if (argc > 1) {
        unsigned x = 0;
        if (sscanf(argv[1], "%x", &x) == 1) addr = (uint8_t)x;
    }

    int fd = open(i2c_path, O_RDWR);
    if (fd < 0) {
        perror("open i2c");
        return 1;
    }

    bme280_linux_ctx_t linux_ctx;
    bme280_hal_t hal;
    bme280_linux_hal_make(&hal, &linux_ctx, fd);

    bme280_config_t cfg = bme280_default_config();
    cfg.log_level = BME280_LOG_INFO;
    cfg.timeout_ms = 50;

    bme280_t sensor;
    bme280_err_t e = bme280_init(&sensor, &hal, addr, &cfg, my_log, NULL);
    if (e != BME280_OK) {
        fprintf(stderr, "init failed: %s\n", bme280_err_str(e));
        close(fd);
        return 2;
    }

    float temp_c = 0.0f;
    e = bme280_read_temperature(&sensor, &temp_c);
    if (e != BME280_OK) {
        fprintf(stderr, "read failed: %s\n", bme280_err_str(e));
        close(fd);
        return 3;
    }

    printf("Temperature: %.2f C\n", temp_c);

    close(fd);
    return 0;
}