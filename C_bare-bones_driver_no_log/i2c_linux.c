
#include "i2c_linux.h"
#include <sys/ioctl.h>
#include <linux/i2c-dev.h>
#include <linux/i2c.h>
#include <unistd.h>
#include <stdio.h>

int i2c_set_slave(int fd, uint8_t addr) {
    if (ioctl(fd, I2C_SLAVE, addr) < 0) return -1;
    return 0;
}

int i2c_read_u8(int fd, uint8_t addr, uint8_t reg, uint8_t *data)
{
    struct i2c_rdwr_ioctl_data packets;
    struct i2c_msg messages[2];

    messages[0].addr  = addr;
    messages[0].flags = 0;
    messages[0].len   = 1;
    messages[0].buf   = &reg;

    messages[1].addr  = addr;
    messages[1].flags = I2C_M_RD;
    messages[1].len   = 1;
    messages[1].buf   = data;

    packets.msgs      = messages;
    packets.nmsgs     = 2;

    if(ioctl(fd, I2C_RDWR, &packets) < 0) {
        perror("i2c_read_u8: I2C_RDWR failed");
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

int i2c_read_buf(int fd, uint8_t addr, uint8_t reg, uint8_t *buf, size_t len)
{
    struct i2c_rdwr_ioctl_data packets;
    struct i2c_msg messages[2];

    messages[0].addr  = addr;
    messages[0].flags = 0;
    messages[0].len   = 1;
    messages[0].buf   = &reg;

    messages[1].addr  = addr;
    messages[1].flags = I2C_M_RD;
    messages[1].len   = len;
    messages[1].buf   = buf;

    packets.msgs      = messages;
    packets.nmsgs     = 2;

    if(ioctl(fd, I2C_RDWR, &packets) < 0) {
        perror("i2c_read_buf: I2C_RDWR failed");
        return -1;
    }
    return 0;
}
