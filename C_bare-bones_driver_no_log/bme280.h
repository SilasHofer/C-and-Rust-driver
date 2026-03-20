#ifndef BME280_H
#define BME280_H
#include <stdint.h>

struct bme280_calib {
    uint16_t dig_T1;
    int16_t dig_T2;
    int16_t dig_T3;
};

struct bme280{
    int fd;
    uint8_t addr;
    struct bme280_calib calib;
    int32_t t_fine;
};

int bme280_init(struct bme280 *dev, int fd, uint8_t addr);

int bme280_read_temperature(struct bme280 *dev, float *temp_c);


#endif