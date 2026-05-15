# User-Space Device Drivers in Embedded Systems: A Comparative Study of C and Rust

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Bachelor's thesis** by Alexander Sundvisson and Silas Hofer  
Submitted to Blekinge Institute of Technology in May 2026

Copyright © 2026 Alexander Sundvisson and Silas Hofer

## Abstract

Device drivers are a critical component of embedded systems, traditionally implemented in C for performance and low-level control. However, C is prone to memory safety issues, which can lead to system instability and security vulnerabilities. This thesis presents a comparative study of two user-space device drivers for the BME280 sensor, one written in C and the other in Rust.

We evaluate both implementations on an embedded Linux system (Raspberry Pi 3B) across several dimensions:
- **Reliability:** Long-term stability and sensor reading consistency.
- **Memory Safety & Fault Behavior:** Robustness against memory errors and I/O faults.
- **Performance:** Latency, CPU utilization, and memory footprint.
- **Architectural Design:** Code complexity and structure.

Our findings indicate that Rust provides safety guarantees at compile-time without a significant runtime performance penalty, making it a compelling and viable alternative to C for this class of user-space drivers.

## Repository Structure

The repository is organized as follows:

```
.
├── C_Driver/                # Source code for the C implementation of the BME280 driver.
├── Rust_Driver/             # Source code for the Rust implementation of the BME280 driver.
├── Python_tests/            # Python scripts for running tests and collecting data.
├── afl_fuzzing/             # Scripts and resources for fuzz testing with AFL++.
├── analysis/                # Scripts and data for static code analysis.
├── LICENSE
└── README.md
```

## Reproducing the Experiments

To reproduce the experiments described in the thesis, you will need the following hardware and software setup.

### Hardware Requirements

- **Raspberry Pi 3 Model B (rev 1.2)** with a 64-bit ARM Cortex-A53 processor.
- **Bosch BME280 Sensor** for temperature, humidity, and pressure readings.
- A microSD card for the operating system.
- Standard peripherals (power supply, etc.).

The BME280 sensor should be connected to the Raspberry Pi's I2C bus:
- **GND** to Pin 9 (Ground)
- **VIN** to Pin 1 (3.3V Power)
- **SCL** to Pin 5 (I2C SCL)
- **SDA** to Pin 3 (I2C SDA)

### Software Requirements

- **Operating System:** Debian GNU/Linux 13 ("Trixie") for `aarch64`.
- **Kernel:** Linux kernel version `6.12.62+rpt-rpi-v8` or compatible.
- **C Compiler:** GCC version 14.2.0 or compatible.
- **Rust Compiler:** `rustc` version 1.85.0 and `cargo` version 1.85.0.
- **Python:** Python 3 with the `psutil` library (`pip install psutil`).
- **Analysis Tools:**
    - `valgrind`: For memory safety analysis of the C driver.
    - `afl++`: For fuzz testing.
    - `cloc`: For counting lines of code.
    - `lizard`: For cyclomatic complexity analysis.

### Setup and Compilation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/SilasHofer/C-and-Rust-driver.git
    cd C-and-Rust-driver
    ```

2.  **Enable I2C on the Raspberry Pi:**
    Use `sudo raspi-config` to enable the I2C interface under "Interfacing Options".

3.  **Compile the C Driver:**
    The Python test scripts expect the binary to be named `c_driver`.
    ```bash
    cd C_Driver
    gcc -std=c11 -O2 -Wall -Wextra main.c bme280.c i2c_linux.c -o c_driver
    cd ..
    ```

4.  **Compile the Rust Driver:**
    The Python test scripts expect the binary to be named `Rust_Driver`. Ensure your `Cargo.toml` package name is `Rust_Driver`.
    ```bash
    cd Rust_Driver
    cargo build --release
    cd ..
    ```
    The compiled binary will be located at `Rust_Driver/target/release/Rust_Driver`.

### Running the Experiments

The Python scripts in the `Python_tests/` directory are used to automate the experiments.

-   **Reliability Test:** Measures long-term stability and sensor consistency. This test runs for 48 hours by default.
    ```bash
    python3 Python_tests/reliability_test.py --parallel
    ```

-   **Performance Test:** Benchmarks latency, CPU usage, and memory footprint.
    ```bash
    python3 Python_tests/performance_test.py
    ```
    Test procedure:
    - System cleanup (kill processes, flush caches, set CPU governor, 2-second settling)
    - **100 warm-up reads** (excluded from analysis, for system stabilization)
    - **1,000 temperature readings** collected as fast as possible using monotonic clock
    - **Repeated 1,000 times per driver** (total: 1,000,000 latency measurements per driver)
    - CPU utilization and memory footprint (RSS) sampled concurrently
    - Latency metrics: mean, standard deviation, 95th percentile

-   **Sequential Validation:** Performs sequential probability ratio test (SPRT) validation of latency differences using two confidence levels:
    ```bash
    python3 Python_tests/sequential_validation.py
    ```
    This test runs two separate SPRT analyses:
    - **99% confidence with MDE ±0.1%:** Detects small practical differences (approximately 43,956 samples per driver)
    - **95% confidence with MDE ±1.0%:** Determines practical equivalence (approximately 24,975 samples per driver)
    
    The test uses interleaved benchmark blocks and stops early once sufficient evidence accumulates.

-   **Memory Safety and Fault Behavior:**
    -   **Valgrind Memcheck (C):**
        ```bash
        python3 Python_tests/run_valgrind_memcheck.py
        ```
        This runs for a fixed duration (approximately 30 seconds per test cycle). Examine the output for invalid reads/writes, memory leaks, and unique error contexts.
    -   **I/O Fault Injection:**
        ```bash
        python3 Python_tests/run_rw_fault.py
        ```

### Detailed Fault Behavior Testing

The thesis evaluates fault behavior through three dedicated test scripts:

#### Graceful Exit & Sensor Disconnection Testing
Run the fault behavior analysis script:
```bash
python3 Python_tests/run_valgrind_fault_behavior.py
```
This script tests:
- **Graceful exit**: SIGINT, SIGTERM, SIGKILL signals
- **Physical sensor disconnection**: Unplug/replug the BME280 from the I2C bus
- **Valgrind memory analysis**: Verifies no memory leaks during fault scenarios
- **I/O fault injection via LD_PRELOAD**: Intercepts system calls to simulate I2C errors

Both drivers should exit cleanly with zero memory leaks under all scenarios.

#### I2C Read/Write Fault Injection (6 Scenarios)
Run the fault injection test:
```bash
python3 Python_tests/run_rw_fault.py
```

This script injects faults via an `LD_PRELOAD` library that intercepts system calls. **Six fault scenarios** tested for each driver:

1. **Deterministic read failures (EIO)** — Every 5th I2C read fails
2. **Deterministic write failures (EIO)** — Every 5th I2C write fails  
3. **Combined read+write failures (EIO)** — Every 5th transaction (read or write) fails
4. **Probabilistic read failures (20% EIO)** — Random reads fail with 20% probability
5. **Device removal (ENODEV)** — Simulates I2C device enumeration failure
6. **Bus timeout (ETIMEDOUT)** — Simulates bus communication timeout

Expected behavior: Both drivers should detect faults immediately and exit with a non-zero exit code. Neither driver implements retry/recovery logic.

#### System Preparation (Automatic)
The `performance_test.py` script automatically handles system preparation before benchmarking:
- Kills any leftover driver processes
- Flushes dirty pages to disk
- Drops filesystem caches (requires passwordless sudo for `/usr/bin/tee` and `/bin/sync`)
- Locks CPU cores to performance governor (requires root)
- Allows 2-second settling period

To enable passwordless sudo for these operations, add to `/etc/sudoers` via `sudo visudo`:
```
pi ALL=(ALL) NOPASSWD: /usr/bin/tee, /bin/sync
```

### Fuzzing

The fuzzing scripts are designed to be run on a Linux host with AFL++ installed.

**Important:** The fuzzing harness uses **mocked I2C functions** (see `afl_fuzzing/C/i2c_mock.c` and `afl_fuzzing/Rust/i2c_mock.rs`) rather than real hardware. This allows controlled exploration of the driver's input handling without depending on sensor availability, but the fuzzing results evaluate robustness against malformed input paths rather than real hardware timing or electrical noise.

**Note:** Ensure the fuzzing binaries are built before running the campaigns. Use the build commands shown in the "Prepare the C/Rust fuzzing target" sections above.

1.  **Prepare the C fuzzing target:**
    ```bash
    cd afl_fuzzing/C
    afl-clang-fast -Iafl_fuzzing/C -IC_Driver -fsanitize=address -g -O0 -o fuzz_target fuzz_target.c i2c_mock.c ../../C_Driver/bme280.c
    cd ../..
    ```

2.  **Prepare the Rust fuzzing target:**
    ```bash
    cd afl_fuzzing/Rust
    AFL_USE_ASAN=1 RUSTFLAGS="-C debuginfo=2 -C opt-level=0" cargo afl build --features fuzzing --bin fuzz_target
    cd ../..
    ```

3.  **Run the fuzzing campaigns:**
    The scripts launch parallel fuzzing instances. You can configure them using environment variables. For the thesis, a 12-hour run on 7 cores was used.

    To run the C fuzzer:
    ```bash
    cd afl_fuzzing
    NUM_CORES=7 CORE_START=1 EXEC_TIMEOUT_MS=40 MAX_TIME_SEC=43200 ./run_fuzzing_c.sh
    ```

    To run the Rust fuzzer:
    ```bash
    cd afl_fuzzing
    NUM_CORES=7 CORE_START=1 EXEC_TIMEOUT_MS=40 MAX_TIME_SEC=43200 ./run_fuzzing_rust.sh
    ```

    Key configuration variables:
    - `NUM_CORES`: Number of parallel fuzzing instances.
    - `CORE_START`: The starting CPU core number.
    - `MAX_TIME_SEC`: Total run time in seconds (e.g., `43200` for 12 hours).
    - `EXEC_TIMEOUT_MS`: Timeout for a single execution in milliseconds.

**Fuzzing Campaign Parameters** (from thesis):
- **7 parallel instances** — Faster exploration with multiple CPU cores
- **40ms timeout** — Realistic for I2C transaction completion time
- **12-hour duration** — Long enough to find edge cases (43,200 seconds)
- **AddressSanitizer** — Detects memory errors during fuzzing
- **Mocked I2C** — Controlled input space without hardware dependencies

**Expected Behavior**: 
- Both drivers should complete the full 12-hour fuzzing campaign without crashes or hangs
- AFL++ should report "0 crashes, 0 hangs" in the final summary
- Corpus size should grow initially and then stabilize (indicating convergence)
- Coverage percentage will vary by implementation, but both should explore meaningful code paths

**Note:** Ensure fuzzing binaries are built before running campaigns (see "Prepare the C/Rust fuzzing target" above).

### Static Code Analysis

To reproduce the architectural design analysis from the thesis:

#### Lines of Code Analysis
```bash
# Install cloc if not already installed
sudo apt-get install cloc

# Count lines in each driver
cloc C_Driver/
cloc Rust_Driver/src/
```

**Interpretation**: Lines of code (non-comment) indicate implementation size. Expect the Rust driver to have more lines than C (50-100% more is typical) due to Rust's explicit error handling, ownership structures, and safety abstractions. This is **not a sign of poor design** — it reflects safety requirements, not extra complexity.

#### Cyclomatic Complexity Analysis
```bash
# Install lizard
pip install lizard

# Analyze C driver
lizard C_Driver/

# For Rust, install rust-code-analysis
cargo install rust-code-analysis
rust-code-analysis -T mir Rust_Driver/src/
```

**Interpretation**: 
- Cyclomatic Complexity (CC) measures decision points in code
- Higher CC = more complex control flow
- **Expect the Rust driver to show higher CC than C** (~2x typical) due to explicit error handling branches
- **Important:** The Rust tool counts each `?` operator as a control-flow branch, which inflates CC for error-propagating functions. These should be read as upper bounds, not direct comparisons to C.

#### Valgrind Memcheck Analysis (C Driver)
```bash
# Run Valgrind on C driver for fixed duration
valgrind --tool=memcheck --leak-check=full ./C_Driver/c_driver 0x76 100 /dev/i2c-1
```

**Interpretation - Look for**:
- Invalid reads/writes: Should be 0
- Use-after-free errors: Should be 0
- Double-free errors: Should be 0
- Definitely lost memory: Should be 0
- Indirectly lost memory: Should be 0
- Still reachable memory at exit: Normal (program cleanup), not a leak

#### Sensor Reading Consistency Analysis
When comparing sensor readings against ground truth:

1. **Calculate mean and standard deviation** of driver readings
2. **Compare to reference readings** collected from separate Pi 3B
3. **Calculate mean deviation** between driver and reference
4. **Look for unexpected spikes** — sudden large temperature changes
5. **Analyze rate of change (dT/dt)** using rolling mean (30-second window):
   - Smooth the data with moving average to reduce noise
   - Compare rates between C and Rust implementations
   - Both should show similar variance with no systematic drift

### Test Execution Guide & Validation

#### Reliability Test (48 hours)

**What to Verify**:
1. Both drivers run continuously without crashes for the full 48-hour period
2. Memory usage remains **stable** (no growth or leaks over time)
3. Sensor readings are **consistent** — no sudden spikes or dropouts
4. Both drivers should produce nearly identical readings when compared side-by-side

**How to Check**:
- Monitor the log output for error messages
- Examine memory profile graphs to ensure flat, non-increasing memory line
- Compare temperature readings: C and Rust should track together (differences <1°C)
- Check that final memory dump shows no "definitely lost" bytes

**Why Results Vary**: Each sensor has a manufacturing calibration offset (~0.2-0.5°C difference). Compare sensor readings to ground truth, not to thesis results.

---

#### Performance Test (1-2 hours)

**What to Verify**:
1. Test completes successfully with 1,000,000 latency measurements (1,000 runs × 1,000 samples)
2. Both drivers show **similar latency** — difference should be <1% 
3. Both drivers show **similar CPU usage** — both should be ~2-3% average
4. Memory footprint should be **stable** — no growth during the test

**Expected Latency Behavior**:
- Absolute values will vary by hardware (Pi 3B vs Pi 4, different OS versions)
- Both drivers should track each other closely (similar distributions, overlapping ranges)
- Rust should **not be significantly slower** than C
- Standard deviation should be consistent between drivers (similar variability patterns)

**How to Check**:
- Verify 1,000,000 samples were collected (not fewer)
- Plot C vs Rust latency distributions — they should overlap substantially
- Check CPU utilization is <5% peak for both drivers
- Verify no memory growth over time

---

#### Sequential Validation Test (20-30 hours total)

**What to Verify**:
1. Test completes both SPRT runs (99% confidence and 95% confidence)
2. Both runs should reach a **statistical decision** (not timeout)
3. Results should show **practical equivalence** at 95% confidence level
4. At 99% confidence, may detect a small difference in favor of C (but <0.2%)

**Expected Outcome**:
- Both implementations should show **practical equivalence** in latency
- If a difference exists, it should be small relative to inherent hardware noise
- Both implementations should be considered **performance-equivalent** for embedded sensor use

**How to Check**:
- Look for convergence in confidence intervals over sequential looks
- At 95% confidence (MDE ±1.0%), C and Rust should fall within the equivalence zone
- The test should reach a decision within 20k-50k samples (not require all 100k+)

---

#### Valgrind Memcheck Test (5-10 minutes)

**What to Verify**:
1. **Zero** invalid reads, invalid writes, use-after-free errors
2. **Zero** double-free or mismatched free/allocation
3. No "definitely lost" or "indirectly lost" memory
4. "Still reachable" memory at exit is expected and normal (program cleanup)

**Expected Interpretation**:
- Some "still reachable" bytes (100-5000 bytes) = normal program cleanup, **not a leak**
- Any "definitely lost" > 0 = **failure** (memory leak)
- Memory usage should plateau (not grow during execution)

**How to Check**:
- Run Valgrind for a fixed duration (30-60 seconds of operation)
- Look at the "LEAK SUMMARY" section in Valgrind output
- "Definitely lost: 0 bytes" = pass
- "Still reachable: <10KB" = expected and OK

---

#### Fault Injection Tests (15-20 minutes total)

**What to Verify for All 6 Fault Scenarios**:
1. Driver detects the fault (error message in stderr)
2. Driver exits with **non-zero exit code** (not 0)
3. No crash or hang (process terminates cleanly)
4. Valgrind reports **zero memory leaks** after fault injection

**Expected Behavior Differences**:
- C driver may exit with code 3 (variable depending on call site)
- Rust driver exits with code 1 (consistent unified error handling)
- Both behaviors are **correct** — different approaches, same safety outcome

**How to Check**:
- Examine exit codes: `echo $?` after driver exits
- Check stderr for error messages about I2C failures
- Verify driver doesn't leave zombie processes: `ps aux | grep c_driver`
- Run Valgrind to confirm zero leaks under fault conditions

---

#### AFL++ Fuzzing Campaign (12+ hours per driver)

**What to Verify**:
1. Test runs for the full 12 hours without interruption
2. **Zero crashes** reported by AFL++
3. **Zero hangs** reported by AFL++
4. Corpus size grows (indicates finding new code paths) but then stabilizes
5. Code coverage > 30% (exact percentage varies by implementation complexity)

**Expected Throughput Behavior** (varies by hardware):
- Throughput will vary based on CPU speed, load, and system configuration
- **Rust typically shows higher throughput** than C during fuzzing (lower overhead)
- Exact execs/second values are hardware-dependent and not meaningful for cross-hardware comparison

**Why Coverage Differs**:
- C driver may show different coverage % than Rust (different number of code edges)
- Rust driver typically has more edges due to explicit error handling
- **Success = Both drivers show growing coverage and find code paths, not an absolute percentage**

**How to Check**:
- Look at AFL++ status screen: "Paths explored", "Unique crashes", "Unique hangs"
- Check final stats: should see "0 crashes, 0 hangs"
- If any crashes are found, investigate — indicates potential bug
- Corpus should grow initially then stabilize (no new crashes = convergence)

---

#### Code Metrics Analysis

**What to Verify**:
1. Rust driver has **more lines of code** than C (50-100% more is typical)
2. Rust driver has **higher cyclomatic complexity** (~2x typical)
3. Both implement the same functionality
4. Rust has isolated `unsafe` blocks (5-10 blocks, all justified)

**Why Metrics Differ**:
- Rust requires explicit error handling (`Result<T>` everywhere)
- Rust requires encapsulation and ownership structures
- These abstractions add lines and complexity, but **prevent entire classes of bugs**

**How to Check**:
```bash
cloc C_Driver/         # Should show ~150 lines
cloc Rust_Driver/src/  # Should show ~200+ lines
lizard C_Driver/       # Get CC metrics
rust-code-analysis -T mir Rust_Driver/src/  # Get Rust metrics
grep -r "unsafe" Rust_Driver/src/  # Count unsafe blocks
```

**Success = Similar LOC ratios to thesis** (Rust ≈ 50-60% more code), **not identical numbers**.

---

### Overall Reproducibility Notes

**Hardware Variations You May See**:
- Different Pi 3B revisions can affect latency and throughput
- Different Debian versions can affect system overhead and memory usage
- Different kernel versions can affect scheduling and I2C timing
- Different ambient temperature can affect sensor calibration and power management

**Qualitative Outcomes to Expect** (should match thesis, not exact numbers):
- ✅ Both drivers equally reliable (no crashes)
- ✅ Both drivers have similar latency (comparable performance)
- ✅ Both drivers use similar CPU resources
- ✅ Rust uses more memory than C (due to safety abstractions)
- ✅ Rust code is more verbose but safer (more LOC, higher CC, explicit error handling)
- ✅ Both handle faults gracefully (exit non-zero, no corruption)
- ✅ Both complete fuzzing campaigns without crashes

**If Your Results Differ from Thesis**:
- Latency and throughput differences from absolute values = **normal** (different hardware)
- Memory usage differences from absolute values = **normal** (different OS/libraries)
- Fuzzing throughput differences from absolute values = **normal** (different CPU, load)
- Crashes or hangs = **investigate** (potential issue with reproduction)
- Rust significantly slower than C = **investigate** (possible compilation issue)
- Qualitative patterns match thesis (reliability, safety, comparable latency) = **success**

**The Goal**: Reproduce the **patterns and qualitative conclusions**, not the exact numbers. The thesis demonstrates that Rust is a viable alternative to C for user-space drivers. Your results should show the same safety and reliability, even if absolute metrics differ.

### Ground Truth Sensor Setup

To validate that driver readings are correct, collect reference sensor data from the Pimoroni BME280 Python library, which provides sensor-native compensation and calibration.

#### Installation

1. **Clone the Pimoroni BME280 library**:
   ```bash
   git clone https://github.com/pimoroni/bme280-python.git
   cd bme280-python
   ```

2. **Install in a virtual environment** (recommended):
   ```bash
   python3 -m venv ~/.virtualenvs/pimoroni
   source ~/.virtualenvs/pimoroni/bin/activate
   pip install .
   ```

   Alternatively, install system-wide:
   ```bash
   pip install pimoroni-bme280
   ```

#### Running Ground Truth Collection

The `source_of_truth/` folder contains modified example scripts for continuous sensor logging:

**Modified `compensated-temperature.py`** (from Pimoroni examples):
- Reads temperature from BME280 with built-in sensor compensation
- Logs readings to `temperature_readings.csv` with ISO 8601 timestamps
- Supports `--duration` (seconds) and `--interval` (sampling interval in seconds)
- Error handling: Retries on sensor failures, exits after 5 consecutive errors
- Resume capability: If CSV exists, resumes from original start time

**Modified `run-bme280.sh`** (wrapper script):
- Runs the Python script with 48-hour duration (172800 seconds) to match reliability test
- Uses `--interval 0` for maximum sampling speed (as fast as I2C allows)

**To run ground truth collection**:

```bash
cd source_of_truth

# Run for 48 hours (matches the reliability test)
./run-bme280.sh

# Or run manually with custom parameters
python3 compensated-temperature.py --duration 172800 --interval 0
```

The script generates `temperature_readings.csv` with two columns:
- Column 1: ISO 8601 timestamp (when reading was taken)
- Column 2: Temperature in °C (sensor-compensated value)

#### Comparing Driver Readings to Ground Truth

Once ground truth data is collected:

1. **Extract timestamps and readings** from driver logs and ground truth CSV
2. **Synchronize by timestamp**: Match driver readings to ground truth readings within ±1 second
3. **Calculate statistics**:
   - Mean temperature for each driver vs. ground truth
   - Standard deviation of readings
   - Mean absolute deviation from ground truth
4. **Compare behavior patterns**:
   - Both should show same temperature trends (thermal response to environment)
   - Both should have similar variance (same sensor, same conditions)
   - Driver readings should stay within ±0.5°C of ground truth (typical BME280 accuracy)

**Note**: The Pimoroni library applies factory calibration coefficients from the sensor's EEPROM, making it an authoritative reference. Your driver implementations should produce similar compensated values if calibration is correctly applied.

### Expected Results and Test Duration

When reproducing the experiments, expect the following durations and outcomes:

| Test | Duration | Expected Outcome |
|------|----------|------------------|
| Reliability Test | 48 hours | Zero crashes, stable memory, consistent sensor readings |
| Performance Test | 30-60 minutes | 1,000,000 latency samples collected; latency distributions compared |
| Sequential Validation | 20-30 minutes | Statistical comparison of C vs Rust latency |
| Valgrind Memcheck | 5-10 minutes per driver | Zero critical memory errors; normal program cleanup memory |
| Fault Injection (I/O) | 5-10 minutes | Both drivers detect faults and exit with non-zero code |
| Fuzzing Campaign | 12+ hours | Zero crashes or hangs; corpus grows then stabilizes |

**Results Directory:** All test results are stored in `Python_tests/Logs/` with subdirectories organized by test type and timestamp.

**Success Criteria:**
- No unexpected crashes or hangs during any test
- Memory usage remains stable (no growth over time)
- Both drivers produce comparable sensor readings
- Fault injection causes graceful exit, not corruption
- Fuzzing discovers no security-critical vulnerabilities

## Known Issues & Script Inconsistencies

**All issues have been fixed.** The following problems were identified and resolved:

### ✅ Fixed: Rust Binary Name Inconsistencies
All test scripts now correctly reference the Rust binary as `Rust_Driver` (matching the Cargo.toml package name).

### ✅ Fixed: Directory Path Inconsistencies  
All test scripts now use the correct directory names:
- `C_Driver` 
- `Rust_Driver`

### ✅ Fixed: AFL++ Target Paths
The fuzzing script now correctly references:
- C target: `afl_fuzzing/C/fuzz_target`
- Rust target: `afl_fuzzing/Rust/target/debug/fuzz_target`

If you encounter any path issues when running tests, please report them as the target platform or build output may differ from expectations.

## Thesis

The full thesis PDF provides a detailed explanation of the methodology, results, and conclusions.

## License

This project is licensed under the **MIT License**.  
Both authors (Alexander Sundvisson and Silas Hofer) retain copyright.

See the full [LICENSE](LICENSE) file for the complete legal text.

