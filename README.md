# C-and-Rust-driver

# C afl fuzzing
NUM_CORES=7 CORE_START=1 EXEC_TIMEOUT_MS=40 MAX_TIME_SEC=43200 ./afl_fuzzing/run_fuzzing_c.sh
afl-clang-fast -Iafl_fuzzing/C -IC_bare-bones_driver_no_log -fsanitize=address -g -O0 -o fuzz_target afl_fuzzing/C/fuzz_target.c afl_fuzzing/C/i2c_mock.c C_bare-bones_driver_no_log/bme280.c

# Rust afl fuzzing
NUM_CORES=7 CORE_START=1 EXEC_TIMEOUT_MS=40 MAX_TIME_SEC=43200 ./afl_fuzzing/run_fuzzing_rust.sh
AFL_USE_ASAN=1 RUSTFLAGS="-C debuginfo=2 -C opt-level=0" cargo afl build --features fuzzing --bin fuzz_target
