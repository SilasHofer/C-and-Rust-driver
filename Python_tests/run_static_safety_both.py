#!/usr/bin/env python3
"""
Static Safety Analysis for BOTH C and Rust BME280 Drivers
→ Final fixed version with explicit PATH for cargo
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
ROOT_DIR = SCRIPT_DIR.parent

C_DIR = ROOT_DIR / "C_Driver"
RUST_DIR = ROOT_DIR / "Rust_Driver"

def _log(msg: str, log_file=None):
    print(msg)
    if log_file:
        log_file.write(msg.rstrip() + "\n")
        log_file.flush()

def _run_command(cmd, cwd, log_file, description="", timeout=300):
    _log(f"\n→ {description}", log_file)
    _log(f"   Running: {' '.join(map(str, cmd))}", log_file)

    # Force cargo into PATH
    env = os.environ.copy()
    cargo_bin = str(Path.home() / ".cargo" / "bin")
    env["PATH"] = f"{cargo_bin}:{env.get('PATH', '')}"

    try:
        result = subprocess.run(
            cmd, cwd=str(cwd), env=env,
            capture_output=True, text=True, timeout=timeout
        )
        output = result.stdout + "\n" + result.stderr
        if log_file:
            log_file.write(output + "\n")
        success = result.returncode == 0
        status = "OK" if success else "Failed"
        _log(f"   → Exit code: {result.returncode} ({status})", log_file)
        return success
    except Exception as e:
        _log(f"   → Error: {e}", log_file)
        return False

def main():
    parser = argparse.ArgumentParser(description="Static safety analysis (no hardware needed)")
    parser.add_argument("--c-dir", type=str, default=str(C_DIR))
    parser.add_argument("--rust-dir", type=str, default=str(RUST_DIR))
    args = parser.parse_args()

    c_dir = Path(args.c_dir)
    rust_dir = Path(args.rust_dir)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = SCRIPT_DIR / "Logs" / f"static_safety_both_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    log_path = out_dir / "static_run.log"
    master_log = open(log_path, "w", encoding="utf-8")

    _log("=" * 80, master_log)
    _log("Static Memory Safety Analysis — BOTH Drivers (No Sensor Required)", master_log)
    _log(f"Started     : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", master_log)
    _log(f"Output      : {out_dir}", master_log)
    _log("=" * 80, master_log)

    # C Driver
    _log("\nC DRIVER ANALYSIS", master_log)
    if c_dir.exists():
        _run_command(["gcc", "-std=c11", "-D_DEFAULT_SOURCE", "-Wall", "-Wextra", "-Wpedantic", "-g", "-O0",
                      "main.c", "bme280.c", "i2c_linux.c", "-o", "c_driver_static"],
                     c_dir, master_log, "Compiling C driver")
        _run_command(["cppcheck", "--enable=all", "--inconclusive", "--quiet", "."],
                     c_dir, master_log, "Running cppcheck")
    else:
        _log("C folder not found", master_log)

    # Rust Driver
    _log("\nRUST DRIVER ANALYSIS", master_log)
    if rust_dir.exists() and (rust_dir / "Cargo.toml").exists():
        _run_command(["cargo", "build", "--release"], rust_dir, master_log, "Building Rust driver")
        _run_command(["cargo", "clippy", "--release", "--all-targets", "--", "-A", "clippy::all",
                      "-W", "clippy::undocumented_unsafe_blocks", "-W", "clippy::missing_safety_doc"],
                     rust_dir, master_log, "Running cargo clippy")
        _run_command(["cargo", "geiger", "--output-format", "Ascii"], rust_dir, master_log, "Running cargo geiger")
        _run_command(["cargo", "audit", "--color", "never"], rust_dir, master_log, "Running cargo audit")

        audit_md = out_dir / "rust_unsafe_audit.md"
        with open(audit_md, "w", encoding="utf-8") as f:
            f.write("# Rust `unsafe` Audit\n\n")
            f.write(f"Generated: {datetime.now().isoformat()}\n\n")
            f.write("Check static_run.log for cargo geiger details.\n\n")
            f.write("For each unsafe item, note:\n")
            f.write("- Purpose: What does it do?\n")
            f.write("- Justification: Why unsafe is needed?\n")
        _log(f"Unsafe audit file: {audit_md}", master_log)
    else:
        _log("Rust folder or Cargo.toml not found", master_log)

    _log(f"\nFull log: {log_path}", master_log)
    master_log.close()

    print(f"\n✅ Done! Results saved in:\n{out_dir}")

if __name__ == "__main__":
    main()