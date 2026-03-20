
#include "i2c_linux.h"
#include <sys/ioctl.h>
#include <linux/i2c-dev.h>
#include <unistd.h>
#include <stdio.h>


int i2c_set_slave(int fd, uint8_t addr) {
    if (ioctl(fd, I2C_SLAVE, addr) < 0) return -1;
    return 0;
}

int i2c_read_u8(int fd, uint8_t reg, uint8_t *data)
{
    if (write(fd, &reg, 1) != 1) {
        perror("Failed to write register address");
        return -1;
    }
    if (read(fd, data, 1) != 1) {
        perror("Failed to read data");
        return -1;
    }
    return 0;
}

int i2c_write_u8(int fd, uint8_t reg, uint8_t data)
{
    uint8_t buf[2] = {reg, data};
    if (write(fd, buf, 2) != 2) {
        perror("Failed to write data");
        return -1;
    }
    return 0;
}

int i2c_read_buf(int fd, uint8_t reg, uint8_t *buf, size_t len)
{
    if (write(fd, &reg, 1) != 1) {
        perror("Failed to write register address");
        return -1;
    }
    if (read(fd, buf, len) != (ssize_t)len) {
        perror("Failed to read buffer");
        return -1;
    }
    return 0;
}
