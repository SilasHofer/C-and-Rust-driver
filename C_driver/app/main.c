#include <stdio.h>
#include <stdint.h>
#include <fcntl.h>
#include <unistd.h>
#include <string.h>

#include "bme280.h"
#include "hal_linux.h"

static void my_log(void *ctx,
                   bme280_log_level_t lvl,
                   bme280_err_t code,
                   const char *msg)
{
    (void)ctx;
    fprintf(stderr, "[LOG %d] %s (%s)\n",
            lvl,
            msg,
            bme280_err_str(code));
}

int main(int argc, char **argv)
{
    const char *i2c_path = "/dev/i2c-1";
    uint8_t addr = 0x76;

    /* Optional CLI argument:
       ./bme280_demo temp
       ./bme280_demo press
       ./bme280_demo hum
       ./bme280_demo all
    */

    bme280_read_mask_t mask = BME280_READ_ALL;

    if (argc > 1) {
        if (strcmp(argv[1], "temp") == 0)
            mask = BME280_READ_TEMP;
        else if (strcmp(argv[1], "press") == 0)
            mask = BME280_READ_PRESS;
        else if (strcmp(argv[1], "hum") == 0)
            mask = BME280_READ_HUM;
        else
            mask = BME280_READ_ALL;
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
    cfg.log_level = BME280_LOG_ERROR;

    bme280_t sensor;

    if (bme280_init(&sensor, &hal, addr, &cfg, my_log, NULL) != BME280_OK) {
        fprintf(stderr, "Init failed\n");
        close(fd);
        return 2;
    }

    while(1){
    bme280_reading_t r;
    if (bme280_read(&sensor, mask, &r) != BME280_OK) {
        fprintf(stderr, "Read failed\n");
        close(fd);
        return 3;
    }

    printf("----- Measurement -----\n");

    if (mask & BME280_READ_TEMP)
        printf("Temperature: %.2f °C\n", r.temperature_c);

    if (mask & BME280_READ_PRESS)
        printf("Pressure:    %.2f Pa\n", r.pressure_pa);

    if (mask & BME280_READ_HUM)
        printf("Humidity:    %.2f %%RH\n", r.humidity_rh);

    printf("-----------------------\n");
    }

    close(fd);
    return 0;
}