#!/usr/bin/env python3
"""
Valgrind Memcheck Only — Improved version for Raspberry Pi
Captures stdout and better error handling.
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
ROOT_DIR = SCRIPT_DIR.parent
C_DIR = ROOT_DIR / "C_bare-bones_driver_no_log"
RUST_DIR = ROOT_DIR / "Rust_driver_no_log"

C_BINARY = "c_driver"
RUST_BINARY = RUST_DIR / "target" / "release" / "bme280_bare_bones"

DEFAULT_DURATION = 45

def _log(msg: str, log_file=None):
    print(msg)
    if log_file:
        log_file.write(msg.rstrip() + "\n")
        log_file.flush()

def run_memcheck(label: str, driver_cmd: list, driver_cwd: Path, duration: int, out_dir: Path, master_log):
    valgrind_log = out_dir / f"{label}_memcheck.log"
    stdout_log = out_dir / f"{label}_stdout.log"

    cmd = [
        "valgrind",
        f"--log-file={valgrind_log}",
        "--tool=memcheck",
        "--leak-check=full",
        "--show-leak-kinds=all",
        "--track-origins=yes",
        "--errors-for-leak-kinds=definite,indirect",
        "--error-exitcode=0",
        "--child-silent-after-fork=yes"
    ] + [str(x) for x in driver_cmd]

    _log(f"\n→ Running Valgrind memcheck on {label} for {duration}s", master_log)

    try:
        with open(stdout_log, "w") as stdout_f:
            proc = subprocess.Popen(
                cmd,
                cwd=str(driver_cwd),
                stdout=stdout_f,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid
            )

            _log(f"   Letting it run for {duration}s and exit naturally...", master_log)
            try:
                proc.wait(timeout=duration + 30)   # waits for clean exit
            except subprocess.TimeoutExpired:
                proc.terminate()

            proc.wait(timeout=15)

        if valgrind_log.exists() and valgrind_log.stat().st_size > 0:
            _log(f"   Success → Log: {valgrind_log}", master_log)
            return valgrind_log
        else:
            _log(f"   Warning: Log file is empty or missing: {valgrind_log}", master_log)
            _log(f"   Check stdout log: {stdout_log}", master_log)
            return None

    except Exception as e:
        _log(f"   Error: {e}", master_log)
        return None

def main():
    parser = argparse.ArgumentParser(description="Run Valgrind memcheck on both drivers")
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION)
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = SCRIPT_DIR / "Logs" / f"valgrind_memcheck_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    log_path = out_dir / "run.log"
    master_log = open(log_path, "w", encoding="utf-8")

    _log("=" * 80, master_log)
    _log("Valgrind Memcheck Analysis — C and Rust Drivers", master_log)
    _log(f"Started  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", master_log)
    _log(f"Duration : {args.duration}s", master_log)
    _log(f"Output   : {out_dir}", master_log)
    _log("=" * 80, master_log)

    if not args.no_build:
        _log("\nBuilding C driver...", master_log)
        subprocess.run(["gcc", "-std=c11", "-D_DEFAULT_SOURCE", "-Wall", "-Wextra", "-g", "-O0",
                        "main.c", "bme280.c", "i2c_linux.c", "-o", C_BINARY], cwd=str(C_DIR))

        _log("Building Rust driver...", master_log)
        subprocess.run(["cargo", "build", "--release"], cwd=str(RUST_DIR))

    # C Driver
    c_out = out_dir / "C"
    c_out.mkdir(exist_ok=True)
    c_cmd = [f"./{C_BINARY}", "0x76", "0"]
    c_log = run_memcheck("c_driver", c_cmd, C_DIR, args.duration, c_out, master_log)

    # Rust Driver
    rust_out = out_dir / "Rust"
    rust_out.mkdir(exist_ok=True)
    rust_cmd = [str(RUST_BINARY), "0x76", "0"]
    rust_log = run_memcheck("rust_driver", rust_cmd, RUST_DIR, args.duration, rust_out, master_log)

    _log("\n" + "="*80, master_log)
    _log("SUMMARY", master_log)
    _log("="*80, master_log)
    _log(f"C Driver log    : {c_log or 'Failed/Empty'}", master_log)
    _log(f"Rust Driver log : {rust_log or 'Failed/Empty'}", master_log)
    _log(f"Full run log    : {log_path}", master_log)

    master_log.close()
    print(f"\n✅ Finished. Results in: {out_dir}")

if __name__ == "__main__":
    if subprocess.call(["which", "valgrind"], stdout=subprocess.DEVNULL) != 0:
        print("valgrind not found → sudo apt install valgrind")
        sys.exit(1)
    main()