use std::fs::File;
use std::io::Read;
use std::process;

use rust_fuzzing::Bme280;

fn main() {
    let path = match std::env::args().nth(1) {
        Some(p) => p,
        None => process::exit(1),
    };

    let mut file = match File::open(path) {
        Ok(f) => f,
        Err(_) => process::exit(1),
    };

    let mut buf = [0u8; 4096];
    let len = match file.read(&mut buf) {
        Ok(n) => n,
        Err(_) => process::exit(1),
    };

    #[cfg(feature = "fuzzing")]
    rust_fuzzing::set_fuzz_input(&buf[..len]);

    let mut dev = match Bme280::new(file, 0x76, false) {
        Ok(d) => d,
        Err(_) => return,
    };

    for _ in 0..5 {
        let _ = dev.read_temperature_c();
    }
}