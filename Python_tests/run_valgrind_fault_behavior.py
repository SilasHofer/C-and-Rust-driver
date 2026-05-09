#!/usr/bin/env python3
"""
Valgrind Fault Behavior Analysis — Including Sensor Disconnection + Out-of-Memory
For thesis subsection: Fault Behaviour Results
"""

import argparse
import os
import resource
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
ROOT_DIR = SCRIPT_DIR.parent
C_DIR = ROOT_DIR / "C_Driver"
RUST_DIR = ROOT_DIR / "Rust_Driver"

C_BINARY = "c_driver"
RUST_BINARY = RUST_DIR / "target" / "release" / "Rust_Driver"

OOM_LIMIT_MB = 12        # ← Realistic sweet spot for these tiny drivers


def _log(msg: str, log_file=None):
    print(msg)
    if log_file:
        log_file.write(msg.rstrip() + "\n")
        log_file.flush()


def parse_valgrind_log(log_path: Path):
    if not log_path.exists():
        return {"definitely_lost": "N/A", "still_reachable": "N/A", "total_errors": "N/A"}
    text = log_path.read_text()
    metrics = {"definitely_lost": "0", "still_reachable": "0", "total_errors": "0"}
    for line in text.splitlines():
        if "definitely lost:" in line:
            metrics["definitely_lost"] = line.split(":")[1].strip().split()[0]
        if "still reachable:" in line:
            metrics["still_reachable"] = line.split(":")[1].strip().split()[0]
        if "ERROR SUMMARY:" in line:
            metrics["total_errors"] = line.split(":")[1].strip().split()[0]
    return metrics


def parse_stdout_for_errors(stdout_path: Path):
    if not stdout_path.exists():
        return "No stdout file"
    text = stdout_path.read_text()
    error_lines = [line.strip() for line in text.splitlines()
                   if any(kw in line.lower() for kw in ["error", "fail", "failed", "i2c", "disconnect", "unable", "timeout", "malloc", "memory"])]
    return "\n".join(error_lines[:8]) if error_lines else "No error messages"


def oom_preexec():
    """Proper preexec function for out-of-memory limit."""
    os.setsid()
    limit = OOM_LIMIT_MB * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def run_test(label: str, driver_cmd: list, driver_cwd: Path, scenario: str, duration: int, out_dir: Path, master_log):
    log_file = out_dir / f"{label}{scenario}.log"
    stdout_file = out_dir / f"{label}{scenario}_stdout.log"

    # --- Command selection ---
    if scenario == "oom":
        cmd = [str(x) for x in driver_cmd]   # NO VALGRIND
    else:
        cmd = [
            "valgrind",
            f"--log-file={log_file}",
            "--tool=memcheck",
            "--leak-check=full",
            "--show-leak-kinds=all",
            "--track-origins=yes",
            "--errors-for-leak-kinds=definite,indirect",
            "--error-exitcode=0",
            "--quiet"
        ] + [str(x) for x in driver_cmd]

    _log(f"\n→ {label} → {scenario}", master_log)

    preexec_fn = os.setsid
    if scenario == "oom":
        _log(f"   *** SIMULATING OUT-OF-MEMORY ({OOM_LIMIT_MB} MB limit) ***", master_log)
        preexec_fn = oom_preexec

    try:
        with open(stdout_file, "w") as f:
            proc = subprocess.Popen(
                cmd,
                cwd=str(driver_cwd),
                stdout=f,
                stderr=subprocess.STDOUT,
                preexec_fn=preexec_fn
            )

            time.sleep(duration // 2)

            # Detect early crash
            if proc.poll() is not None:
                _log("   Process exited early (likely OOM)", master_log)
                fault_time = time.time()
            else:
                fault_time = time.time()

            # --- Scenario actions ---
            if scenario == "sensor_disconnect":
                _log("   *** UNPLUG THE SENSOR NOW *** (waiting 15 seconds)", master_log)
                time.sleep(15)
            elif scenario == "graceful":
                os.killpg(os.getpgid(proc.pid), signal.SIGINT)
            elif scenario == "sigterm":
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            elif scenario == "sigkill":
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)

            time.sleep(duration // 2)

            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()   # IMPORTANT FIX
                _log("   Forced kill after timeout", master_log)

        detection_time = time.time() - fault_time
        exit_code = proc.returncode

        # --- Parse outputs ---
        if scenario == "oom":
            driver_msgs = parse_stdout_for_errors(stdout_file)

            # Safe signal detection
            if exit_code is None:
                signal_name = "UNKNOWN"
            elif exit_code < 0:
                signal_name = signal.Signals(-exit_code).name
            else:
                signal_name = "None"

            oom_detected = (
                exit_code in (137, -9) or
                "memory" in driver_msgs.lower()
            )

            # Write OOM log
            with open(log_file, "w") as lf:
                lf.write("OOM TEST RESULT\n")
                lf.write("=" * 40 + "\n")
                lf.write(f"Exit code      : {exit_code}\n")
                lf.write(f"Signal         : {signal_name}\n")
                lf.write(f"OOM suspected  : {oom_detected}\n\n")
                lf.write("Driver output:\n")
                lf.write(driver_msgs + "\n")

            metrics = {
                "definitely_lost": "N/A",
                "still_reachable": "N/A",
                "total_errors": "N/A"
            }

        else:
            metrics = parse_valgrind_log(log_file)
            driver_msgs = parse_stdout_for_errors(stdout_file)

        # --- Logging ---
        _log(f"   Finished. Log: {log_file.name}", master_log)
        _log(f"   Detection time : {detection_time:.1f}s", master_log)
        _log(f"   Exit code      : {exit_code}", master_log)

        if scenario != "oom":
            _log(f"   Still reachable: {metrics['still_reachable']} bytes", master_log)

        if driver_msgs != "No error messages":
            _log(f"   Driver messages: {driver_msgs}", master_log)

        return {
            "log_file": log_file,
            "stdout_file": stdout_file,
            "detection_time": round(detection_time, 1),
            "exit_code": exit_code,
            "metrics": metrics,
            "driver_messages": driver_msgs
        }

    except Exception as e:
        _log(f"   Error: {e}", master_log)
        return None


def main():
    parser = argparse.ArgumentParser(description="Valgrind Fault Behaviour Analysis for thesis")
    parser.add_argument("--duration", type=int, default=40)
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = SCRIPT_DIR / "Logs" / f"fault_behavior_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    log_path = out_dir / "summary.log"
    with open(log_path, "w", encoding="utf-8") as master_log:
        _log("=" * 85, master_log)
        _log("Fault Behaviour & Memory Cleanup Analysis (incl. Sensor Disconnection + OOM)", master_log)
        _log(f"Started     : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", master_log)
        _log(f"Duration    : {args.duration}s", master_log)
        _log(f"Output      : {out_dir}", master_log)
        _log("=" * 85, master_log)

        if not args.no_build:
            _log("\nBuilding drivers...", master_log)
            subprocess.run(["gcc", "-std=c11", "-D_DEFAULT_SOURCE", "-Wall", "-Wextra", "-g", "-O0",
                            "main.c", "bme280.c", "i2c_linux.c", "-o", C_BINARY], cwd=str(C_DIR))
            subprocess.run(["cargo", "build", "--release"], cwd=str(RUST_DIR))

        scenarios = ["graceful", "sigterm", "sigkill", "oom", "sensor_disconnect"]
        results = {}

        for scenario in scenarios:

            if scenario == "sensor_disconnect":
                _log("\nWaiting 20 seconds to reconnect sensor...", master_log)
                time.sleep(20)
            _log(f"\n{'='*70}", master_log)
            _log(f"SCENARIO: {scenario.upper()}", master_log)
            _log(f"{'='*70}", master_log)

            c_cmd = [f"./{C_BINARY}", "0x76", "0"]
            rust_cmd = [str(RUST_BINARY), "0x76", "0"]

            c_result = run_test("C", c_cmd, C_DIR, scenario, args.duration, out_dir, master_log)
            rust_result = run_test("Rust", rust_cmd, RUST_DIR, scenario, args.duration, out_dir, master_log)

            results[f"C_{scenario}"] = c_result
            results[f"Rust_{scenario}"] = rust_result

        # === STRUCTURED THESIS TABLE ===
        _log("\n" + "="*85, master_log)
        _log("STRUCTURED RESULTS TABLE – Copy directly into your thesis", master_log)
        _log("="*85, master_log)

        table_lines = [
            "Scenario             | Driver | Detection time (s) | Exit code | Definitely lost | Still reachable | Driver error messages | Notes"
        ]
        for scenario in scenarios:
            for driver in ["C", "Rust"]:
                key = f"{driver}_{scenario}"
                res = results.get(key)
                if res:
                    m = res["metrics"]
                    msgs = "Yes" if res["driver_messages"] != "No error messages" else "None"
                    ec = res.get("exit_code", "N/A")
                    table_lines.append(
                        f"{scenario:20} | {driver:6} | {res['detection_time']:18} | {str(ec):9} | {m['definitely_lost']:15} | "
                        f"{m['still_reachable']:15} | {msgs:21} | -"
                    )

        _log("\n".join(table_lines), master_log)
        _log("\nFull raw logs (Valgrind + stdout) are in this folder.", master_log)

    print(f"\n✅ Test completed!")
    print(f"Results + ready-to-paste table saved in: {out_dir}")
    print(f"Summary log: {log_path}")


if __name__ == "__main__":
    if subprocess.call(["which", "valgrind"], stdout=subprocess.DEVNULL) != 0:
        print("valgrind not found → sudo apt install valgrind")
        sys.exit(1)
    main()