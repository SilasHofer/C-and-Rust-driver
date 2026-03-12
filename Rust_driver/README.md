# Bare-bones Rust BME280 driver

This crate mirrors the minimal C example in `../C_bare-bones_driver`.

It does the following:
- opens `/dev/i2c-1`
- selects the BME280 I2C address, default `0x76`
- validates chip ID `0x60`
- performs a soft reset
- reads temperature calibration registers
- triggers a forced measurement
- prints the compensated temperature in Celsius

Build and run:

```bash
cargo run --release
cargo run --release -- 77
```

The optional argument is the sensor I2C address in hexadecimal, with or without the `0x` prefix.