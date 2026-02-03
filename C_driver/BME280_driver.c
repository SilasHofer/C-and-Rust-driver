#include <errno.h>
#include <fcntl.h>
#include <linux/i2c-dev.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

static int i2c_set_slave(int fd, uint8_t addr) {
    if (ioctl(fd, I2C_SLAVE, addr) < 0) return -1;
    return 0;
}

static int i2c_read_u8(int fd, uint8_t reg, uint8_t *data)
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

int main(int argc, char **argv) {
    const char *dev = "/dev/i2c-1";
    uint8_t addr = 0x76;
    if(argc > 1) {
        unsigned x = 0;
        if (sscanf(argv[1], "%x", &x) == 1) {
            addr = (uint8_t)x;
        }
    }

    int fd = open(dev, O_RDWR);
    if (fd < 0) {
        perror("Failed to open I2C device");
        return 1;
    }

    if (i2c_set_slave(fd, addr) < 0) {
        perror("Failed to set I2C slave address");
        close(fd);
        return 1;
    }

    uint8_t chip_id = 0;
    if (i2c_read_u8(fd, 0xD0, &chip_id) < 0) {
        close(fd);
        return 1;
    }

    printf("BME280 Chip ID: 0x%02X\n", chip_id);

    close(fd);
    return 0;
}
