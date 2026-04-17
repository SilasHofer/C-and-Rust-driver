# Rust `unsafe` Audit

Generated: 2026-04-17T15:05:09

## Summary

- Total `unsafe` occurrences: **5**
- `unsafe fn`: 0
- `unsafe impl`: 0
- `unsafe trait`: 0
- `unsafe block`: 5

## Findings

Fill in **Purpose** and **Justification** for each entry.

### 1. `unsafe block` — `src/lib.rs:191`

**Purpose:** _TODO — what hardware/FFI interaction does this perform?_

**Justification:** _TODO — why can't this be done in safe Rust?_

```rust

fn i2c_set_slave(file: &File, addr: u8) -> Result<(), DriverError> {
    let ret = unsafe { libc::ioctl(file.as_raw_fd(), I2C_SLAVE, addr as libc::c_ulong) };
    if ret < 0 {
        return Err(io::Error::last_os_error().into());
    }
    Ok(())
}

fn i2c_read_u8(file: &File, addr: u8, reg: u8) -> Result<u8, DriverError> {
```

### 2. `unsafe block` — `src/lib.rs:206`

**Purpose:** _TODO — what hardware/FFI interaction does this perform?_

**Justification:** _TODO — why can't this be done in safe Rust?_

```rust
fn i2c_write_u8(file: &File, reg: u8, data: u8) -> Result<(), DriverError> {
    let payload = [reg, data];
    let written = unsafe {
        libc::write(
            file.as_raw_fd(),
            payload.as_ptr() as *const libc::c_void,
            payload.len(),
        )
    };

```

### 3. `unsafe block` — `src/lib.rs:241`

**Purpose:** _TODO — what hardware/FFI interaction does this perform?_

**Justification:** _TODO — why can't this be done in safe Rust?_

```rust
    };

    let ret = unsafe { libc::ioctl(file.as_raw_fd(), I2C_RDWR, &mut data) };
    if ret < 0 {
        return Err(io::Error::last_os_error().into());
    }

    Ok(())
}

```

### 4. `unsafe block` — `src/lib.rs:250`

**Purpose:** _TODO — what hardware/FFI interaction does this perform?_

**Justification:** _TODO — why can't this be done in safe Rust?_

```rust

fn flock_lock(file: &File) -> Result<(), DriverError> {
    let ret = unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX) };
    if ret < 0 {
        return Err(io::Error::last_os_error().into());
    }
    Ok(())
}

fn flock_unlock(file: &File) -> Result<(), DriverError> {
```

### 5. `unsafe block` — `src/lib.rs:258`

**Purpose:** _TODO — what hardware/FFI interaction does this perform?_

**Justification:** _TODO — why can't this be done in safe Rust?_

```rust

fn flock_unlock(file: &File) -> Result<(), DriverError> {
    let ret = unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_UN) };
    if ret < 0 {
        return Err(io::Error::last_os_error().into());
    }
    Ok(())
}

fn compensate_temperature(adc_t: i32, calib: Bme280Calib, t_fine: &mut i32) -> i32 {
```

