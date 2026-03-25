#!/usr/bin/env python3
"""
Driver Test Runner for Raspberry Pi 3
Compiles and runs the C driver, Rust driver, or both.

Assumed folder layout (relative to this script's location):
  ../C_bare-bones_driver_no_log/   ← C source files
  ../Rust_driver_no_log/           ← Rust project (has Cargo.toml)
"""

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


# ── Paths (resolved relative to this script's location) ──────────────────────
SCRIPT_DIR  = Path(__file__).parent.resolve()
ROOT_DIR    = SCRIPT_DIR.parent

C_DIR       = ROOT_DIR / "C_bare-bones_driver_no_log"
C_BINARY    = "c_driver"               # compiled output name (relative to C_DIR)

RUST_DIR    = ROOT_DIR / "Rust_driver_no_log"
# Binary ends up at target/debug/<package-name> — must match [package] name in Cargo.toml
RUST_BINARY = RUST_DIR / "target" / "debug" / "rust_driver_no_log"
# ─────────────────────────────────────────────────────────────────────────────


def run_step(label: str, cmd: list, cwd: Path, timeout: int, log_file) -> bool:
    """Run a shell command, print output, return True on success."""
    print(f"\n  $ {' '.join(str(c) for c in cmd)}")
    try:
        result = subprocess.run(
            [str(c) for c in cmd],
            cwd=str(cwd),
            timeout=timeout,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        output = result.stdout or ""
        print(output, end="")
        if log_file:
            log_file.write(f"[{label}]\n{output}\n")
        if result.returncode != 0:
            print(f"  ✗ '{label}' exited with code {result.returncode}")
            return False
        return True

    except FileNotFoundError as e:
        msg = f"  ✗ Command not found: {e}"
        print(msg)
        if log_file:
            log_file.write(msg + "\n")
        return False

    except subprocess.TimeoutExpired:
        msg = f"  ✗ '{label}' timed out after {timeout}s"
        print(msg)
        if log_file:
            log_file.write(msg + "\n")
        return False


TEMP_SAMPLES    = 1000  # default number of readings (override with --samples)
TEMP_PATTERN    = "Temperature:"  # matched against each line of driver output
LOG_EVERY_N     = 10    # log a temperature reading every N samples (e.g. 10 or 50)


def capture_temperature_readings(label: str, cmd: list, cwd: Path, timeout: int, log_file, samples: int) -> bool:
    """
    Launch a driver process, capture `samples` temperature lines, then kill it.
    Returns True if the target count was reached without error.
    """
    start_dt = datetime.now()
    start    = time.monotonic()

    print(f"\n  Start time : {start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    print(f"  Waiting for {samples} temperature readings...\n")

    if log_file:
        log_file.write(f"[{label}]\n")
        log_file.write(f"Start time : {start_dt.strftime('%Y-%m-%d %H:%M:%S')}\n")
        log_file.write(f"Target     : {samples} readings (logging every {LOG_EVERY_N})\n\n")

    count = 0
    ok    = False

    try:
        proc = subprocess.Popen(
            [str(c) for c in cmd],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        for line in proc.stdout:
            line = line.rstrip()

            if TEMP_PATTERN in line:
                count += 1
                # Always print to terminal
                print(f"  [{count}/{samples}] {line}")
                # Only write to log every LOG_EVERY_N readings
                if log_file and count % LOG_EVERY_N == 0:
                    log_file.write(f"[{count}/{samples}] {line}\n")
            else:
                # Non-temperature lines (errors, warnings etc.) always logged
                print(f"  {line}")
                if log_file:
                    log_file.write(line + "\n")

            if count >= samples:
                ok = True
                break

            if time.monotonic() - start > timeout:
                print(f"\n  ✗ Timed out after {timeout}s ({count}/{samples} readings)")
                if log_file:
                    log_file.write(f"\nTimed out after {timeout}s ({count}/{samples} readings)\n")
                break

    except FileNotFoundError as e:
        print(f"  ✗ Command not found: {e}")
        if log_file:
            log_file.write(f"Command not found: {e}\n")
        return False

    finally:
        try:
            proc.kill()
            proc.wait()
        except Exception:
            pass

    end_dt  = datetime.now()
    elapsed = time.monotonic() - start

    print(f"\n  End time   : {end_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Readings   : {count}/{samples}")
    print(f"  Duration   : {elapsed:.2f}s")

    if log_file:
        log_file.write(f"\nEnd time   : {end_dt.strftime('%Y-%m-%d %H:%M:%S')}\n")
        log_file.write(f"Readings   : {count}/{samples}\n")
        log_file.write(f"Duration   : {elapsed:.2f}s\n")

    return ok


def build_and_run_c(timeout: int, log: bool, samples: int) -> bool:
    print(f"\n{'─' * 50}")
    print("  C Driver — compile + run")
    print(f"{'─' * 50}")

    log_file = None
    if log:
        log_path = SCRIPT_DIR / f"c_driver_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        log_file = open(log_path, "w")
        print(f"  Logging to: {log_path.name}")

    try:
        # Step 1 — compile
        ok = run_step(
            "gcc compile",
            [
                "gcc", "-std=c11", "-D_DEFAULT_SOURCE",
                "-Wall", "-Wextra", "-O2",
                "main.c", "bme280.c", "i2c_linux.c",
                "-o", C_BINARY,
            ],
            cwd=C_DIR,
            timeout=timeout,
            log_file=log_file,
        )
        if not ok:
            return False

        # Step 2 — run and capture 1000 temperature readings
        ok = capture_temperature_readings(
            "run",
            [f"./{C_BINARY}"],
            cwd=C_DIR,
            timeout=timeout,
            log_file=log_file,
            samples=samples,
        )

        print(f"\n  {'✓ PASSED' if ok else '✗ FAILED'}")
        return ok

    finally:
        if log_file:
            log_file.close()


def build_and_run_rust(timeout: int, log: bool, samples: int) -> bool:
    print(f"\n{'─' * 50}")
    print("  Rust Driver — cargo build + cargo run")
    print(f"{'─' * 50}")

    log_file = None
    if log:
        log_path = SCRIPT_DIR / f"rust_driver_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        log_file = open(log_path, "w")
        print(f"  Logging to: {log_path.name}")

    try:
        # Step 1 — cargo build
        ok = run_step(
            "cargo build",
            ["cargo", "build", "--release"],
            cwd=RUST_DIR,
            timeout=timeout,
            log_file=log_file,
        )
        if not ok:
            return False

        # Step 2 — run and capture 1000 temperature readings
        ok = capture_temperature_readings(
            "cargo run",
            ["cargo", "run", "--release"],
            cwd=RUST_DIR,
            timeout=timeout,
            log_file=log_file,
            samples=samples,
        )

        print(f"\n  {'✓ PASSED' if ok else '✗ FAILED'}")
        return ok

    finally:
        if log_file:
            log_file.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compile and run C/Rust drivers on Raspberry Pi 3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_drivers.py --c              # Compile + run C driver only
  python run_drivers.py --rust           # Compile + run Rust driver only
  python run_drivers.py --both           # Compile + run both drivers
  python run_drivers.py --both --log     # Run both and save logs to Python_tests/
  python run_drivers.py --both --timeout 120
    python run_drivers.py --parallel       # Alternate C/Rust readings, compare results
        """,
    )

    driver_group = parser.add_mutually_exclusive_group(required=False)
    driver_group.add_argument("--c",    action="store_true", help="Compile and run the C driver only")
    driver_group.add_argument("--rust", action="store_true", help="Compile and run the Rust driver only")
    driver_group.add_argument("--both", action="store_true", help="Compile and run both drivers")

    parser.add_argument(
        "--samples",
        type=int,
        default=TEMP_SAMPLES,
        metavar="N",
        help=f"Number of temperature readings to capture before stopping (default: {TEMP_SAMPLES})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        metavar="SECONDS",
        help="Per-step timeout in seconds (default: 60)",
    )
    parser.add_argument(
        "--log",
        action="store_true",
        help="Save each driver's output to a timestamped .log file in Python_tests/",
    )

    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Alternate C/Rust readings, compare results at end (1000 total readings)",
    )

    args = parser.parse_args()
    # If --parallel is set, force --both and ignore --c/--rust
    if args.parallel:
        args.both = True
        args.c = False
        args.rust = False
    # If neither --c, --rust, nor --both is set, and not parallel, show error
    if not (args.c or args.rust or args.both or args.parallel):
        parser.error("one of the arguments --c --rust --both is required")
    return args


def main() -> None:
    args = parse_args()

    print("=" * 50)
    print("  Raspberry Pi 3 — Driver Test Runner")
    print(f"  Started : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Root    : {ROOT_DIR}")
    print("=" * 50)


    if args.parallel:
        print(f"\n{'─' * 50}")
        print("  Parallel Mode: Alternating C/Rust readings")
        print(f"{'─' * 50}")

        c_temps = []
        rust_temps = []
        total_reads = args.samples
        timeout = args.timeout

        def get_temp_from_line(line):
            # Expects line like: "Temperature: <value> ..."
            import re
            m = re.search(r"Temperature:\s*([-+]?[0-9]*\.?[0-9]+)", line)
            if m:
                return float(m.group(1))
            return None

        for i in range(1, total_reads + 1):
            if i % 2 == 1:
                # Odd: C driver
                label = f"C_read_{i}"
                cmd = [f"./{C_BINARY}"]
                cwd = C_DIR
            else:
                # Even: Rust driver
                label = f"Rust_read_{i}"
                cmd = ["cargo", "run", "--release"]
                cwd = RUST_DIR

            try:
                proc = subprocess.Popen(
                    [str(c) for c in cmd],
                    cwd=str(cwd),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                temp_val = None
                for line in proc.stdout:
                    if TEMP_PATTERN in line:
                        temp_val = get_temp_from_line(line)
                        print(f"  [{i}/{total_reads}] {label}: {line.strip()}")
                        break
                proc.kill()
                proc.wait()
                if temp_val is not None:
                    if i % 2 == 1:
                        c_temps.append(temp_val)
                    else:
                        rust_temps.append(temp_val)
                else:
                    print(f"  ✗ No temperature found for {label}")
            except Exception as e:
                print(f"  ✗ Error running {label}: {e}")

        # Compare results
        print(f"\n{'=' * 50}")
        print("  Comparison of Sensor Readings")
        print(f"{'=' * 50}")
        min_len = min(len(c_temps), len(rust_temps))
        diffs = []
        for idx in range(min_len):
            diff = abs(c_temps[idx] - rust_temps[idx])
            diffs.append(diff)
            print(f"  Pair {idx+1}: C={c_temps[idx]:.2f}  Rust={rust_temps[idx]:.2f}  |Δ|={diff:.4f}")
        if diffs:
            print(f"\n  Average |Δ|: {sum(diffs)/len(diffs):.4f}")
            print(f"  Max |Δ|: {max(diffs):.4f}")
        else:
            print("  No valid pairs to compare.")
        print(f"{'=' * 50}\n")
        sys.exit(0)

    results: dict[str, bool] = {}

    if args.c or args.both:
        results["C"] = build_and_run_c(args.timeout, args.log, args.samples)

    if args.rust or args.both:
        results["Rust"] = build_and_run_rust(args.timeout, args.log, args.samples)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'=' * 50}")
    print("  Summary")
    print(f"{'=' * 50}")
    all_passed = True
    for driver, passed in results.items():
        icon = "✓" if passed else "✗"
        print(f"  {icon}  {driver} driver — {'PASSED' if passed else 'FAILED'}")
        if not passed:
            all_passed = False

    print(f"{'=' * 50}\n")
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()