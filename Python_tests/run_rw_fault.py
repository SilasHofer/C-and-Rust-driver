#!/usr/bin/env python3
"""
Read/Write Fault Injection Test — BME280 C & Rust drivers
For thesis subsection: Fault Behaviour Results — I2C Read/Write Errors

Injects faults via LD_PRELOAD (i2c_fault_inject.so), which intercepts:
  - ioctl(I2C_RDWR)  → read path
  - write(fd, 2)     → write path

Scenarios tested for each driver:
  read_every_n   — fail every Nth I2C read  (EIO)
  write_every_n  — fail every Nth I2C write (EIO)
  both_every_n   — fail every Nth of either (EIO)
  read_prob      — random 20% read failures (EIO)
  enodev         — simulate device removal  (ENODEV)
  etimedout      — simulate bus timeout     (ETIMEDOUT)
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Paths (same layout as existing script) ──────────────────────────────────
SCRIPT_DIR  = Path(__file__).parent.resolve()
ROOT_DIR    = SCRIPT_DIR.parent
C_DIR       = ROOT_DIR / "C_bare-bones_driver_no_log"
RUST_DIR    = ROOT_DIR / "Rust_driver_no_log"

C_BINARY    = C_DIR / "c_driver"
RUST_BINARY = RUST_DIR / "target" / "release" / "bme280_bare_bones"
INJECTOR_SO = SCRIPT_DIR / "i2c_fault_inject.so"

# ── Fault scenarios ──────────────────────────────────────────────────────────
SCENARIOS = [
    {
        "name":        "read_every_n",
        "description": "Fail every 5th I2C read (EIO)",
        "env": {
            "FAULT_TARGET":  "read",
            "FAULT_MODE":    "every_n",
            "FAULT_EVERY_N": "5",
            "FAULT_ERRNO":   str(5),   # EIO
        },
    },
    {
        "name":        "write_every_n",
        "description": "Fail every 5th I2C write (EIO)",
        "env": {
            "FAULT_TARGET":  "write",
            "FAULT_MODE":    "every_n",
            "FAULT_EVERY_N": "5",
            "FAULT_ERRNO":   str(5),
        },
    },
    {
        "name":        "both_every_n",
        "description": "Fail every 5th read or write (EIO)",
        "env": {
            "FAULT_TARGET":  "both",
            "FAULT_MODE":    "every_n",
            "FAULT_EVERY_N": "5",
            "FAULT_ERRNO":   str(5),
        },
    },
    {
        "name":        "read_prob",
        "description": "20% random read failure rate (EIO)",
        "env": {
            "FAULT_TARGET": "read",
            "FAULT_MODE":   "prob",
            "FAULT_PROB":   "0.2",
            "FAULT_ERRNO":  str(5),
        },
    },
    {
        "name":        "enodev",
        "description": "Simulate device removal on reads (ENODEV)",
        "env": {
            "FAULT_TARGET":  "read",
            "FAULT_MODE":    "every_n",
            "FAULT_EVERY_N": "5",
            "FAULT_ERRNO":   str(19),  # ENODEV
        },
    },
    {
        "name":        "etimedout",
        "description": "Simulate I2C bus timeout on reads (ETIMEDOUT)",
        "env": {
            "FAULT_TARGET":  "read",
            "FAULT_MODE":    "every_n",
            "FAULT_EVERY_N": "5",
            "FAULT_ERRNO":   str(110), # ETIMEDOUT
        },
    },
]


def _log(msg: str, log_file=None):
    print(msg)
    if log_file:
        log_file.write(msg.rstrip() + "\n")
        log_file.flush()


def parse_stdout_for_errors(stdout_path: Path) -> str:
    if not stdout_path.exists():
        return "No stdout file"
    text = stdout_path.read_text()
    keywords = ["error", "fail", "failed", "i2c", "timeout", "errno", "panic",
                 "unable", "disconnect", "invalid", "DriverError"]
    error_lines = [
        line.strip() for line in text.splitlines()
        if any(kw in line.lower() for kw in keywords)
    ]
    return "\n      ".join(error_lines[:8]) if error_lines else "None"


def count_successful_reads(stdout_path: Path) -> int:
    """Count 'Temperature:' lines as a proxy for successful read cycles."""
    if not stdout_path.exists():
        return 0
    return sum(1 for line in stdout_path.read_text().splitlines()
               if line.startswith("Temperature:"))


def run_test(
    label: str,
    driver_cmd: list,
    driver_cwd: Path,
    scenario: dict,
    duration: int,
    out_dir: Path,
    master_log,
) -> dict:
    scenario_name = scenario["name"]
    stdout_file   = out_dir / f"{label}_{scenario_name}_stdout.log"

    # Build environment: inherit current env, add injector variables
    env = os.environ.copy()
    env["LD_PRELOAD"] = str(INJECTOR_SO)
    env.update(scenario["env"])

    cmd = [str(x) for x in driver_cmd]

    _log(f"\n  → {label} | {scenario_name}", master_log)
    _log(f"    {scenario['description']}", master_log)

    start_time = time.time()

    try:
        with open(stdout_file, "w") as f:
            proc = subprocess.Popen(
                cmd,
                cwd=str(driver_cwd),
                stdout=f,
                stderr=subprocess.STDOUT,
                env=env,
                preexec_fn=os.setsid,
            )

        # Wait for the full duration, watching for early exit
        exited_early = False
        exit_time    = None

        while time.time() - start_time < duration:
            rc = proc.poll()
            if rc is not None:
                exited_early = True
                exit_time    = time.time() - start_time
                break
            time.sleep(0.2)

        detection_time = exit_time if exited_early else duration

        # Terminate if still running
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                proc.wait()

        exit_code      = proc.returncode
        driver_msgs    = parse_stdout_for_errors(stdout_file)
        read_successes = count_successful_reads(stdout_file)

        # Determine outcome
        if exited_early:
            outcome = f"Crashed/exited (rc={exit_code}) after {detection_time:.1f}s"
        else:
            outcome = f"Ran full duration ({duration}s), exit rc={exit_code}"

        _log(f"    Outcome        : {outcome}", master_log)
        _log(f"    Successful reads: {read_successes}", master_log)
        _log(f"    Driver messages: {driver_msgs}", master_log)

        return {
            "label":            label,
            "scenario":         scenario_name,
            "exited_early":     exited_early,
            "detection_time":   round(detection_time, 1),
            "exit_code":        exit_code,
            "read_successes":   read_successes,
            "driver_messages":  driver_msgs,
            "stdout_file":      stdout_file,
        }

    except Exception as e:
        _log(f"    ERROR: {e}", master_log)
        return {
            "label":           label,
            "scenario":        scenario_name,
            "exited_early":    None,
            "detection_time":  None,
            "exit_code":       None,
            "read_successes":  0,
            "driver_messages": str(e),
            "stdout_file":     stdout_file,
        }


def build_drivers(master_log):
    _log("\nBuilding C driver...", master_log)
    r = subprocess.run(
        ["gcc", "-std=c11", "-D_DEFAULT_SOURCE", "-Wall", "-Wextra", "-g", "-O0",
         "main.c", "bme280.c", "i2c_linux.c", "-o", str(C_BINARY)],
        cwd=str(C_DIR),
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        _log(f"  C build FAILED:\n{r.stderr}", master_log)
    else:
        _log("  C driver built OK", master_log)

    _log("Building Rust driver...", master_log)
    r = subprocess.run(
        ["cargo", "build", "--release"],
        cwd=str(RUST_DIR),
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        _log(f"  Rust build FAILED:\n{r.stderr}", master_log)
    else:
        _log("  Rust driver built OK", master_log)


def build_injector(master_log):
    _log("\nBuilding LD_PRELOAD injector...", master_log)
    r = subprocess.run(
        ["gcc", "-shared", "-fPIC", "-o", str(INJECTOR_SO),
         str(SCRIPT_DIR / "i2c_fault_inject.c"), "-ldl"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        _log(f"  Injector build FAILED:\n{r.stderr}", master_log)
        sys.exit(1)
    _log(f"  Injector built: {INJECTOR_SO}", master_log)


def main():
    parser = argparse.ArgumentParser(
        description="Read/Write fault injection test for BME280 C and Rust drivers"
    )
    parser.add_argument(
        "--duration", type=int, default=40,
        help="Seconds to run each driver per scenario (default: 40)"
    )
    parser.add_argument(
        "--no-build", action="store_true",
        help="Skip building drivers and injector"
    )
    parser.add_argument(
        "--scenarios", nargs="+",
        choices=[s["name"] for s in SCENARIOS],
        help="Run only specific scenarios (default: all)"
    )
    args = parser.parse_args()

    active_scenarios = (
        [s for s in SCENARIOS if s["name"] in args.scenarios]
        if args.scenarios else SCENARIOS
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir   = SCRIPT_DIR / "Logs" / f"rw_fault_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    log_path = out_dir / "summary.log"
    with open(log_path, "w", encoding="utf-8") as master_log:
        _log("=" * 85, master_log)
        _log("Read/Write Fault Injection — BME280 C vs Rust drivers", master_log)
        _log(f"Started    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", master_log)
        _log(f"Duration   : {args.duration}s per driver per scenario", master_log)
        _log(f"Injector   : {INJECTOR_SO}", master_log)
        _log(f"Output     : {out_dir}", master_log)
        _log("=" * 85, master_log)

        if not args.no_build:
            build_injector(master_log)
            build_drivers(master_log)

        c_cmd    = [str(C_BINARY),    "0x76", "0"]
        rust_cmd = [str(RUST_BINARY), "0x76", "0"]

        all_results = []

        for scenario in active_scenarios:
            _log(f"\n{'='*70}", master_log)
            _log(f"SCENARIO : {scenario['name'].upper()}", master_log)
            _log(f"           {scenario['description']}", master_log)
            _log(f"{'='*70}", master_log)

            c_result    = run_test("C",    c_cmd,    C_DIR,    scenario, args.duration, out_dir, master_log)
            rust_result = run_test("Rust", rust_cmd, RUST_DIR, scenario, args.duration, out_dir, master_log)

            all_results.append(c_result)
            all_results.append(rust_result)

        # ── Results table ────────────────────────────────────────────────────
        _log("\n" + "=" * 85, master_log)
        _log("STRUCTURED RESULTS TABLE — Copy directly into your thesis", master_log)
        _log("=" * 85, master_log)

        col = "{:<18} {:<6} {:<16} {:<12} {:<10} {:<30}"
        header = col.format(
            "Scenario", "Driver", "Detection (s)",
            "Exit code", "Reads OK", "Driver error messages"
        )
        _log(header, master_log)
        _log("-" * 85, master_log)

        for r in all_results:
            if r is None:
                continue
            det  = str(r["detection_time"]) if r["detection_time"] is not None else "N/A"
            ec   = str(r["exit_code"])      if r["exit_code"]      is not None else "N/A"
            msgs = (r["driver_messages"] or "None").splitlines()[0][:30]
            _log(col.format(
                r["scenario"], r["label"], det,
                ec, str(r["read_successes"]), msgs
            ), master_log)

        _log("\nFull stdout logs (including injector stderr) are in this folder.", master_log)

    print(f"\n✅ Test completed!")
    print(f"Results saved in : {out_dir}")
    print(f"Summary log      : {log_path}")


if __name__ == "__main__":
    # Quick sanity check
    if not INJECTOR_SO.exists() and "--no-build" in sys.argv:
        print(f"ERROR: {INJECTOR_SO} not found. Remove --no-build to build it first.")
        sys.exit(1)
    main()
