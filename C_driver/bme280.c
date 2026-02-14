#include <stdint.h>
#include <stdio.h>
#include <unistd.h>

#include "bme280.h"
#include "i2c_linux.h"


int bme280_init(struct bme280 *dev, int fd, uint8_t addr)
{
    uint8_t id;
    uint8_t calib_buf[6];

    dev->fd = fd;
    dev->addr = addr;

    if (i2c_set_slave(fd, addr) < 0)
        return -1;

    if (i2c_read_u8(fd, 0xD0, &id) < 0)
        return -1;

    if (id != 0x60) {
        fprintf(stderr, "Unexpected chip ID: 0x%02X\n", id);
        return -1;
    }

    // Soft reset
    i2c_write_u8(fd, 0xE0, 0xB6);
    usleep(2000); // 2 ms reset time

    // Read temperature calibration (0x88..0x8D)
    if (i2c_read_buf(fd, 0x88, calib_buf, sizeof(calib_buf)) < 0)
        return -1;

    dev->calib.dig_T1 = (uint16_t)((calib_buf[1] << 8) | calib_buf[0]);
    dev->calib.dig_T2 = (int16_t)((calib_buf[3] << 8) | calib_buf[2]);
    dev->calib.dig_T3 = (int16_t)((calib_buf[5] << 8) | calib_buf[4]);

    // Humidity oversampling x1
    i2c_write_u8(fd, 0xF2, 0x01);

    // Temp + pressure oversampling x1, forced mode
    i2c_write_u8(fd, 0xF4, 0x25);

    return 0;
}

int bme280_read_temperature(struct bme280 *dev, float *temp_c)
{
    uint8_t status = 0;
    uint8_t buf[3];
    int32_t adc_T;
    int32_t var1, var2;
    int32_t T;

    if (!dev || !temp_c)
        return -1;

    if (i2c_set_slave(dev->fd, dev->addr) < 0)
        return -1;

    // Trigger a forced measurement (temp + pressure oversampling x1)
    if (i2c_write_u8(dev->fd, 0xF4, 0x25) < 0)
        return -1;

    // Wait until measurement done (bit 3 of status clears)
    for (int i = 0; i < 20; i++) {
        if (i2c_read_u8(dev->fd, 0xF3, &status) < 0)
            return -1;
        if ((status & 0x08) == 0)
            break;
        usleep(2000);
    }

    if (i2c_read_buf(dev->fd, 0xFA, buf, sizeof(buf)) < 0)
        return -1;

    adc_T = (int32_t)((buf[0] << 12) | (buf[1] << 4) | (buf[2] >> 4));

    var1 = ((((adc_T >> 3) - ((int32_t)dev->calib.dig_T1 << 1))) *
            ((int32_t)dev->calib.dig_T2)) >> 11;

    var2 = (((((adc_T >> 4) - ((int32_t)dev->calib.dig_T1)) *
              ((adc_T >> 4) - ((int32_t)dev->calib.dig_T1))) >> 12) *
            ((int32_t)dev->calib.dig_T3)) >> 14;

    dev->t_fine = var1 + var2;
    T = (dev->t_fine * 5 + 128) >> 8; // 0.01 C

    *temp_c = T / 100.0f;
    return 0;
}
