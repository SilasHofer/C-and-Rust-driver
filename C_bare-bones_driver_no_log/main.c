#define _XOPEN_SOURCE 700
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/types.h>

#include "i2c_linux.h"
#include "bme280.h"

int main(int argc, char **argv)
{
    const char *i2c_path = "/dev/i2c-1";
    uint8_t addr = 0x76;
    float hz = 0;

    if (argc > 1) {
        unsigned x = 0;
        if (sscanf(argv[1], "%x", &x) == 1) {
            addr = (uint8_t)x;
        }
    }

    if (argc > 2) {
        hz = atof(argv[2]);
    }

    useconds_t delay = 0;
    if (hz > 0.0) {
        delay = (useconds_t)(1000000.0 / hz);
    }

    int fd = open(i2c_path, O_RDWR);
    if (fd < 0) {
        perror("Failed to open I2C device");
        return 1;
    }

    struct bme280 sensor;

    if (bme280_init(&sensor, fd, addr) < 0) {
        fprintf(stderr, "Failed to initialize BME280 sensor\n");
        close(fd);
        return 2;
    }

    printf("BME280 sensor initialized successfully\n");
    while(1){
        float temp_c = 0.0f;
        if (bme280_read_temperature(&sensor, &temp_c) < 0) {
            fprintf(stderr, "Failed to read temperature\n");
            close(fd);
            return 3;
        }

        printf("Temperature: %.2f C\n", temp_c);
        fflush(stdout);
                
        // Only sleep if hz > 0
        if (hz > 0.0) {
            usleep(delay);
        }
    }
    close(fd);
    return 0;
}