#include <stdint.h>
#include <stddef.h>

// Fuzz input buffer
static const uint8_t *fuzz_data;
static size_t fuzz_size;
static size_t fuzz_pos;

// Simple state
static int call_count = 0;
static uint8_t last_reg = 0;

// Called by fuzz_target to provide input
void i2c_mock_set_input(const uint8_t *data, size_t size) {
    fuzz_data = data;
    fuzz_size = size;
    fuzz_pos = 0;
    call_count = 0;
    last_reg = 0;
}

// Internal helper
static uint8_t next_byte() {
    if (fuzz_pos >= fuzz_size) return 0;
    return fuzz_data[fuzz_pos++];
}

// ---- Mocked I2C API ----

int i2c_set_slave(int fd, uint8_t addr) {
    call_count++;
    // Lower failure rate for fuzzing
    uint8_t ctrl = next_byte();
if (ctrl == 0xAA) return -1;
    return 0;
}

int i2c_read_u8(int fd, uint8_t addr, uint8_t reg, uint8_t *data) {
    call_count++;
    last_reg = reg;
    if (!data) return -1;

    // Lower failure rate
    uint8_t ctrl = next_byte();
if (ctrl == 0xAA) return -1;

    switch (reg) {
            case 0xD0:
                *data = 0x60; // always valid
                break;

        case 0xF3: // status register
            *data = next_byte() & ~0x08; // usually "not busy"
            break;

        default:
            *data = next_byte();
            break;
    }

    return 0;
}

int i2c_read_buf(int fd, uint8_t addr, uint8_t reg, uint8_t *buf, size_t len) {
    call_count++;
    last_reg = reg;
    if (!buf) return -1;

    uint8_t ctrl = next_byte();
if (ctrl == 0xAA) return -1;

    for (size_t i = 0; i < len; i++) {
        uint8_t v = next_byte();
        if (reg == 0x88) buf[i] = v ^ (i * 31);  // calibration
        else if (reg == 0xFA) buf[i] = v ^ (call_count * 13); // temp
        else buf[i] = v;
    }

    return 0;
}

int i2c_write_u8(int fd, uint8_t reg, uint8_t data) {
    call_count++;
    last_reg = reg;
    uint8_t ctrl = next_byte();
if (ctrl == 0xAA) return -1;
    return 0;
}