#![allow(static_mut_refs)]

static mut DATA: *const u8 = std::ptr::null();
static mut SIZE: usize = 0;
static mut POS: usize = 0;
static mut CALL_COUNT: i32 = 0;
static mut LAST_REG: u8 = 0;

pub fn i2c_mock_set_input(data: *const u8, len: usize) {
    unsafe {
        DATA = data;
        SIZE = len;
        POS = 0;
        CALL_COUNT = 0;
        LAST_REG = 0;
    }
}

fn next_byte() -> u8 {
    unsafe {
        if POS >= SIZE {
            return 0;
        }
        let b = *DATA.add(POS);
        POS += 1;
        b
    }
}

// --- Replace low-level functions ---

use std::fs::File;
use crate::DriverError;

pub fn i2c_set_slave(_file: &File, _addr: u8) -> Result<(), DriverError> {
    unsafe {
        CALL_COUNT += 1;
    }

    let ctrl = next_byte();
    if ctrl == 0xAA {
        return Err(std::io::Error::from_raw_os_error(1).into());
    }
    Ok(())
}

pub fn i2c_read_u8(_file: &File, _addr: u8, reg: u8) -> Result<u8, DriverError> {
    unsafe {
        CALL_COUNT += 1;
        LAST_REG = reg;
    }

    let ctrl = next_byte();
    if ctrl == 0xAA {
        return Err(std::io::Error::from_raw_os_error(1).into());
    }

    Ok(match reg {
        0xD0 => 0x60,
        0xF3 => next_byte() & !0x08,
        _ => next_byte(),
    })
}

pub fn i2c_read_buf(
    _file: &File,
    _addr: u8,
    reg: u8,
    buf: &mut [u8],
) -> Result<(), DriverError> {
    unsafe {
        CALL_COUNT += 1;
        LAST_REG = reg;
    }

    let ctrl = next_byte();
    if ctrl == 0xAA {
        return Err(std::io::Error::from_raw_os_error(1).into());
    }

    for (i, out) in buf.iter_mut().enumerate() {
        let v = next_byte();
        *out = if reg == 0x88 {
            v ^ ((i as u8).wrapping_mul(31))
        } else if reg == 0xFA {
            let call_factor = unsafe { (CALL_COUNT as u8).wrapping_mul(13) };
            v ^ call_factor
        } else {
            v
        };
    }
    Ok(())
}

pub fn i2c_write_u8(_file: &File, _reg: u8, _data: u8) -> Result<(), DriverError> {
    unsafe {
        CALL_COUNT += 1;
        LAST_REG = _reg;
    }

    let ctrl = next_byte();
    if ctrl == 0xAA {
        return Err(std::io::Error::from_raw_os_error(1).into());
    }
    Ok(())
}