use std::fmt;
use std::fs::{File, OpenOptions};
use std::io;

#[cfg(not(feature = "fuzzing"))]
use std::os::fd::AsRawFd;

#[cfg(not(feature = "fuzzing"))]
use std::thread;

#[cfg(not(feature = "fuzzing"))]
use std::time::Duration;

#[cfg(not(feature = "fuzzing"))]
use libc;

#[cfg(feature = "fuzzing")]
mod i2c_mock;

#[cfg(feature = "fuzzing")]
use i2c_mock::*;

const I2C_SLAVE: libc::c_ulong = 0x0703;
const I2C_RDWR: libc::c_ulong = 0x0707;
const I2C_M_RD: u16 = 0x0001;

const REG_CHIP_ID: u8 = 0xD0;
const REG_RESET: u8 = 0xE0;
const REG_STATUS: u8 = 0xF3;
const REG_CTRL_HUM: u8 = 0xF2;
const REG_CTRL_MEAS: u8 = 0xF4;
const REG_TEMP_MSB: u8 = 0xFA;
const REG_CALIB_START: u8 = 0x88;

const CHIP_ID: u8 = 0x60;
const RESET_COMMAND: u8 = 0xB6;
const CTRL_HUM_X1: u8 = 0x01;
const CTRL_MEAS_TEMP_PRESS_X1_FORCED: u8 = 0x25;
const STATUS_MEASURING_MASK: u8 = 0x08;

#[cfg(not(feature = "fuzzing"))]
const RESET_DELAY_MS: u64 = 2;

#[cfg(not(feature = "fuzzing"))]
const POLL_DELAY_MS: u64 = 2;

const MAX_MEASUREMENT_POLLS: usize = 20;
const LOCK_FILE: &str = "/tmp/bme280.lock";

#[derive(Debug)]
pub enum DriverError {
    Io(io::Error),
    InvalidChipId(u8),
    MeasurementTimeout,
}

impl fmt::Display for DriverError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Io(err) => write!(f, "I/O error: {err}"),
            Self::InvalidChipId(id) => write!(f, "unexpected chip ID: 0x{id:02X}"),
            Self::MeasurementTimeout => write!(f, "measurement did not complete in time"),
        }
    }
}

impl std::error::Error for DriverError {}

impl From<io::Error> for DriverError {
    fn from(err: io::Error) -> Self {
        Self::Io(err)
    }
}

#[derive(Clone, Copy, Debug)]
struct Bme280Calib {
    dig_t1: u16,
    dig_t2: i16,
    dig_t3: i16,
}

pub struct Bme280 {
    file: File,
    addr: u8,
    calib: Bme280Calib,
    t_fine: i32,
    lock_file: Option<File>,
}

impl Bme280 {
    pub fn new(file: File, addr: u8, use_lock: bool) -> Result<Self, DriverError> {
        let lock_file = if use_lock {
            Some(
                OpenOptions::new()
                    .create(true)
                    .read(true)
                    .write(true)
                    .open(LOCK_FILE)?,
            )
        } else {
            None
        };

        let mut sensor = Self {
            file,
            addr,
            calib: Bme280Calib {
                dig_t1: 0,
                dig_t2: 0,
                dig_t3: 0,
            },
            t_fine: 0,
            lock_file,
        };

        i2c_set_slave(&sensor.file, sensor.addr)?;

        let chip_id = i2c_read_u8(&sensor.file, sensor.addr, REG_CHIP_ID)?;
        if chip_id != CHIP_ID {
            return Err(DriverError::InvalidChipId(chip_id));
        }

        i2c_write_u8(&sensor.file, REG_RESET, RESET_COMMAND)?;

        #[cfg(not(feature = "fuzzing"))]
        thread::sleep(Duration::from_millis(RESET_DELAY_MS));

        let mut calib_buf = [0u8; 6];
        i2c_read_buf(&sensor.file, sensor.addr, REG_CALIB_START, &mut calib_buf)?;

        sensor.calib = Bme280Calib {
            dig_t1: u16::from_le_bytes([calib_buf[0], calib_buf[1]]),
            dig_t2: i16::from_le_bytes([calib_buf[2], calib_buf[3]]),
            dig_t3: i16::from_le_bytes([calib_buf[4], calib_buf[5]]),
        };

        i2c_write_u8(&sensor.file, REG_CTRL_HUM, CTRL_HUM_X1)?;
        i2c_write_u8(&sensor.file, REG_CTRL_MEAS, CTRL_MEAS_TEMP_PRESS_X1_FORCED)?;

        Ok(sensor)
    }

    pub fn read_temperature_c(&mut self) -> Result<f32, DriverError> {
        i2c_set_slave(&self.file, self.addr)?;
        i2c_write_u8(&self.file, REG_CTRL_MEAS, CTRL_MEAS_TEMP_PRESS_X1_FORCED)?;

        let mut measurement_complete = false;

        for _ in 0..MAX_MEASUREMENT_POLLS {
            let status = i2c_read_u8(&self.file, self.addr, REG_STATUS)?;
            if status & STATUS_MEASURING_MASK == 0 {
                measurement_complete = true;
                break;
            }

            #[cfg(not(feature = "fuzzing"))]
            thread::sleep(Duration::from_millis(POLL_DELAY_MS));
        }

        if !measurement_complete {
            return Err(DriverError::MeasurementTimeout);
        }

        let mut buf = [0u8; 3];
        i2c_read_buf(&self.file, self.addr, REG_TEMP_MSB, &mut buf)?;

        let adc_t = ((buf[0] as i32) << 12)
            | ((buf[1] as i32) << 4)
            | ((buf[2] as i32) >> 4);

        let temperature = compensate_temperature(adc_t, self.calib, &mut self.t_fine);

        Ok(temperature as f32 / 100.0)
    }
}

#[cfg(not(feature = "fuzzing"))]
fn i2c_set_slave(file: &File, addr: u8) -> Result<(), DriverError> {
    let ret = unsafe { libc::ioctl(file.as_raw_fd(), I2C_SLAVE, addr as libc::c_ulong) };
    if ret < 0 {
        return Err(io::Error::last_os_error().into());
    }
    Ok(())
}

#[cfg(not(feature = "fuzzing"))]
fn i2c_read_u8(file: &File, addr: u8, reg: u8) -> Result<u8, DriverError> {
    let mut data = [0u8; 1];
    i2c_read_buf(file, addr, reg, &mut data)?;
    Ok(data[0])
}

#[cfg(not(feature = "fuzzing"))]
fn i2c_write_u8(file: &File, reg: u8, data: u8) -> Result<(), DriverError> {
    let payload = [reg, data];
    let written = unsafe {
        libc::write(
            file.as_raw_fd(),
            payload.as_ptr() as *const libc::c_void,
            payload.len(),
        )
    };

    if written != payload.len() as isize {
        return Err(io::Error::last_os_error().into());
    }

    Ok(())
}

#[cfg(not(feature = "fuzzing"))]
fn i2c_read_buf(
    file: &File,
    addr: u8,
    reg: u8,
    buf: &mut [u8],
) -> Result<(), DriverError> {
    #[repr(C)]
    struct I2cMsg {
        addr: u16,
        flags: u16,
        len: u16,
        buf: *mut u8,
    }

    #[repr(C)]
    struct I2cRdwrIoctlData {
        msgs: *mut I2cMsg,
        nmsgs: u32,
    }

    let mut reg_buf = [reg];

    let write_msg = I2cMsg {
        addr: addr as u16,
        flags: 0,
        len: 1,
        buf: reg_buf.as_mut_ptr(),
    };

    let read_msg = I2cMsg {
        addr: addr as u16,
        flags: I2C_M_RD,
        len: buf.len() as u16,
        buf: buf.as_mut_ptr(),
    };

    let mut msgs = [write_msg, read_msg];

    let mut data = I2cRdwrIoctlData {
        msgs: msgs.as_mut_ptr(),
        nmsgs: 2,
    };

    let ret = unsafe { libc::ioctl(file.as_raw_fd(), I2C_RDWR, &mut data) };
    if ret < 0 {
        return Err(io::Error::last_os_error().into());
    }

    Ok(())
}

fn compensate_temperature(adc_t: i32, calib: Bme280Calib, t_fine: &mut i32) -> i32 {
    let var1 = ((((adc_t >> 3) - ((calib.dig_t1 as i32) << 1))) * (calib.dig_t2 as i32)) >> 11;

    let delta = (adc_t >> 4) - (calib.dig_t1 as i32);
    let var2 = (((delta * delta) >> 12) * (calib.dig_t3 as i32)) >> 14;

    *t_fine = var1 + var2;
    (*t_fine * 5 + 128) >> 8
}

#[cfg(feature = "fuzzing")]
pub fn set_fuzz_input(data: &[u8]) {
    i2c_mock::i2c_mock_set_input(data.as_ptr(), data.len());
}