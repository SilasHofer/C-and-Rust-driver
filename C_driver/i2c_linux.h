#ifndef I2C_LINUX_H
#define I2C_LINUX_H

#include <stdint.h>
#include <stddef.h>

int i2c_set_slave(int fd, uint8_t addr);

int i2c_read_u8(int fd, uint8_t reg, uint8_t *data);

int i2c_write_u8(int fd, uint8_t reg, uint8_t data);

int i2c_read_buf(int fd, uint8_t reg, uint8_t *buf, size_t len);

#endif