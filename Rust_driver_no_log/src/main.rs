use std::env;
use std::fs::OpenOptions;
use std::time::Duration;
use std::thread;

use bme280_bare_bones::{Bme280, DriverError};

const I2C_PATH: &str = "/dev/i2c-1";
const DEFAULT_ADDR: u8 = 0x76;

fn main() {
    if let Err(err) = run() {
        eprintln!("{err}");
        std::process::exit(exit_code_for(&err));
    }
}

fn run() -> Result<(), DriverError> {
    // Parse I2C address
    let addr = env::args()
        .nth(1)
        .map(|arg| parse_address(&arg))
        .transpose()?  // parse_address returns Result<u8, DriverError>
        .unwrap_or(DEFAULT_ADDR);

    // Parse Hz argument safely
    let hz = env::args()
        .nth(2)
        .map(|arg| {
            arg.parse::<f64>().map_err(|e| {
                DriverError::Io(std::io::Error::new(
                    std::io::ErrorKind::InvalidInput,
                    format!("invalid Hz value '{arg}': {e}"),
                ))
            })
        })
        .transpose()?  // convert Option<Result<..>> -> Result<Option<..>>
        .unwrap_or(0.0); // default 0 = max stress, no delay

    // Compute optional delay
    let delay = if hz > 0.0 {
        Some(Duration::from_micros((1_000_000.0 / hz) as u64))
    } else {
        None
    };

    // Open I2C device and initialize sensor
    let file = OpenOptions::new().read(true).write(true).open(I2C_PATH)?;
    let mut sensor = Bme280::new(file, addr)?;

    println!("BME280 sensor initialized successfully");

    loop {
        let temp_c = sensor.read_temperature_c()?;
        println!("Temperature: {temp_c:.2} C");

        if let Some(d) = delay {
            thread::sleep(d);
        }
    }
}

fn parse_address(value: &str) -> Result<u8, DriverError> {
    let trimmed = value
        .strip_prefix("0x")
        .or_else(|| value.strip_prefix("0X"))
        .unwrap_or(value);

    u8::from_str_radix(trimmed, 16).map_err(|err| {
        DriverError::Io(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            format!("invalid I2C address '{value}': {err}"),
        ))
    })
}

fn exit_code_for(err: &DriverError) -> i32 {
    match err {
        DriverError::Io(io_err) if io_err.kind() == std::io::ErrorKind::InvalidInput => 1,
        DriverError::Io(_) => 1,
        DriverError::InvalidChipId(_) => 2,
        DriverError::MeasurementTimeout => 3,
    }
}

#[cfg(test)]
mod tests {
    use super::parse_address;

    #[test]
    fn parses_hex_addresses_with_or_without_prefix() {
        assert_eq!(parse_address("76").unwrap(), 0x76);
        assert_eq!(parse_address("0x77").unwrap(), 0x77);
    }
}
