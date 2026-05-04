#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>

#include "bme280.h"

// Provided by i2c_mock.c
void i2c_mock_set_input(const uint8_t *data, size_t size);

int main(int argc, char **argv) {
    if (argc < 2) return 1;

    FILE *f = fopen(argv[1], "rb");
    if (!f) return 1;

    uint8_t buf[4096];
    size_t len = fread(buf, 1, sizeof(buf), f);
    fclose(f);

    // Feed fuzz data into mock I2C
    i2c_mock_set_input(buf, len);

    struct bme280 dev;

    // Initialize and only continue if successful
    if (bme280_init(&dev, 0, 0x76) == 0) {
        float temp = 0.0f;
        for (int i = 0; i < 5; i++) {  // multiple reads per input
            bme280_read_temperature(&dev, &temp);
        }
    }

    return 0;
}