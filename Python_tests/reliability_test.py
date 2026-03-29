#!/usr/bin/env python3
"""
Driver Test Runner for Raspberry Pi 3
Compiles and runs the C driver, Rust driver, or both.

Assumed folder layout (relative to this script's location):
  ../C_bare-bones_driver_no_log/   <- C source files
  ../Rust_driver_no_log/           <- Rust project (has Cargo.toml)
"""

import argparse
import math
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
import psutil
import threading
import re as _re


# -- Paths (resolved relative to this script's location) ----------------------
SCRIPT_DIR  = Path(__file__).parent.resolve()
ROOT_DIR    = SCRIPT_DIR.parent

C_DIR       = ROOT_DIR / "C_bare-bones_driver_no_log"
C_BINARY    = "c_driver"

RUST_DIR    = ROOT_DIR / "Rust_driver_no_log"
RUST_BINARY = RUST_DIR / "target" / "debug" / "rust_driver_no_log"
# -----------------------------------------------------------------------------


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
            print(f"  x '{label}' exited with code {result.returncode}")
            return False
        return True

    except FileNotFoundError as e:
        msg = f"  x Command not found: {e}"
        print(msg)
        if log_file:
            log_file.write(msg + "\n")
        return False

    except subprocess.TimeoutExpired:
        msg = f"  x '{label}' timed out after {timeout}s"
        print(msg)
        if log_file:
            log_file.write(msg + "\n")
        return False


TEMP_PATTERN = "Temperature:"
LOG_EVERY_N  = 100
COMPILE_TIMEOUT = 120


def _latency_stats(intervals: list[float]) -> dict:
    """
    Compute mean, std-dev, and worst-case latency from a list of
    inter-reading intervals (in seconds). Returns an empty dict if
    there are no intervals recorded.
    """
    n = len(intervals)
    if n == 0:
        return {}
    mean = sum(intervals) / n
    variance = sum((x - mean) ** 2 for x in intervals) / n
    return {
        "n":        n,
        "mean_ms":  mean * 1000,
        "std_ms":   math.sqrt(variance) * 1000,
        "worst_ms": max(intervals) * 1000,
    }


def _resource_stats(mem_samples: list[float]) -> dict:
    n_mem = len(mem_samples)
    if n_mem == 0:
        return {}

    result = {
        "mem_mean_kb": (sum(mem_samples) / n_mem) / 1024,
        "mem_peak_kb": max(mem_samples) / 1024,
        "mem_n":       n_mem,
    }

    if n_mem >= 2:
        xs    = list(range(n_mem))
        x_bar = sum(xs) / n_mem
        y_bar = sum(mem_samples) / n_mem
        num   = sum((xs[i] - x_bar) * (mem_samples[i] - y_bar) for i in range(n_mem))
        den   = sum((xs[i] - x_bar) ** 2 for i in range(n_mem))
        result["mem_slope_kb_per_sample"] = (num / den if den != 0 else 0.0) / 1024

    return result


def _print_latency_stats(stats: dict, log_file) -> None:
    if not stats:
        return
    lines = [
        f"  Latency (inter-reading interval, n={stats['n']}):",
        f"    Mean        : {stats['mean_ms']:8.3f} ms",
        f"    Std-dev     : {stats['std_ms']:8.3f} ms",
        f"    Worst-case  : {stats['worst_ms']:8.3f} ms",
    ]
    for line in lines:
        print(line)
        if log_file:
            log_file.write(line.lstrip() + "\n")


def _print_resource_stats(stats: dict, log_file) -> None:
    if not stats:
        return
    lines = ["  Resource usage (driver process):"]
    if "mem_mean_kb" in stats:
        lines += [
            f"    Mem mean    : {stats['mem_mean_kb']:8.1f} KB",
            f"    Mem peak    : {stats['mem_peak_kb']:8.1f} KB",
        ]
    if "mem_slope_kb_per_sample" in stats:
        slope = stats["mem_slope_kb_per_sample"]
        trend = "growing ⚠" if slope > 0.01 else ("shrinking" if slope < -0.01 else "stable ✓")
        lines.append(f"    Mem trend   : {slope:+.4f} KB/sample ({trend})")

    for line in lines:
        print(line)
        if log_file:
            log_file.write(line.lstrip() + "\n")

def _sensor_stats(temp_values: list[float]) -> dict:
    n = len(temp_values)
    if n == 0:
        return {}
    mean     = sum(temp_values) / n
    variance = sum((x - mean) ** 2 for x in temp_values) / n
    std      = math.sqrt(variance)
    min_v    = min(temp_values)
    max_v    = max(temp_values)

    # Spike: reading differs from both its neighbours by more than 3x the
    # global std-dev. Ignores first and last reading (no two neighbours).
    spikes = 0
    for i in range(1, n - 1):
        prev_diff = abs(temp_values[i] - temp_values[i - 1])
        next_diff = abs(temp_values[i] - temp_values[i + 1])
        if prev_diff > 3 * std and next_diff > 3 * std:
            spikes += 1

    return {
        "n":      n,
        "mean":   mean,
        "std":    std,
        "min":    min_v,
        "max":    max_v,
        "range":  max_v - min_v,
        "spikes": spikes,
    }


def _print_sensor_stats(stats: dict, log_file) -> None:
    if not stats:
        return
    lines = [
        f"  Sensor reading variation (n={stats['n']}):",
        f"    Mean        : {stats['mean']:8.3f} °C",
        f"    Std-dev     : {stats['std']:8.3f} °C",
        f"    Min         : {stats['min']:8.3f} °C",
        f"    Max         : {stats['max']:8.3f} °C",
        f"    Range       : {stats['range']:8.3f} °C",
        f"    Spikes (>3σ): {stats['spikes']}",
    ]
    for line in lines:
        print(line)
        if log_file:
            log_file.write(line.lstrip() + "\n")

class ResourceSampler:
    def __init__(self, pid: int, interval: float = 0.02):
        self.proc        = psutil.Process(pid)
        self.interval    = interval
        self.mem_samples = []
        self._running    = False
        self._thread     = None

    def start(self):
        self._running = True
        self._thread  = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join()

    def _run(self):
        while self._running:
            try:
                self.mem_samples.append(self.proc.memory_info().rss)
            except psutil.NoSuchProcess:
                break
            time.sleep(self.interval)



def capture_temperature_readings(
    label: str, cmd: list, cwd: Path, timeout: int, log_file
) -> bool:
    start_dt     = datetime.now()
    global_start = time.monotonic()

    print(f"\n  Start time : {start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Running for {timeout}s with auto-restart on failure...\n")

    if log_file:
        log_file.write(f"[{label}]\n")
        log_file.write(f"Start time : {start_dt.strftime('%Y-%m-%d %H:%M:%S')}\n")
        log_file.write(f"Timeout    : {timeout}s (auto-restart enabled)\n\n")

    total_count    = 0
    restart_count  = 0
    intervals      = []
    last_ts        = None
    all_mem_samples = []
    failure_times  = []
    temp_values    = []

    while time.monotonic() - global_start < timeout:
        proc           = None
        error_detected = False
        failure_reason = ""

        try:
            proc = subprocess.Popen(
                [str(c) for c in cmd],
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            sampler = ResourceSampler(proc.pid)
            sampler.start()

            for line in proc.stdout:
                if time.monotonic() - global_start >= timeout:
                    break

                line = line.rstrip()

                if "Failed to" in line or "error" in line.lower() or "panicked at" in line:
                    error_detected = True
                    failure_reason = line

                if TEMP_PATTERN in line:
                    now = time.monotonic()
                    if last_ts is not None:
                        intervals.append(now - last_ts)
                    last_ts = now

                    total_count += 1
                    print(f"  [{total_count}] {line}")

                    if log_file and total_count % LOG_EVERY_N == 0:
                        log_file.write(f"[{total_count}] {line}\n")

                    m = _re.search(r"Temperature:\s*([-+]?[0-9]*\.?[0-9]+)", line)
                    if m:
                        temp_values.append(float(m.group(1)))

                else:
                    print(f"  {line}")
                    if log_file:
                        log_file.write(line + "\n")

                if error_detected:
                    break

            sampler.stop()
            proc.kill()
            proc.wait()

            all_mem_samples.extend(sampler.mem_samples)

            if error_detected:
                restart_count += 1
                failure_times.append(time.monotonic() - global_start)
                ts  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                msg = f"\n  ⚠ [{ts}] Restarting driver (#{restart_count}) due to: {failure_reason}"
                print(msg)
                if log_file:
                    log_file.write(msg + "\n")
                time.sleep(0.5)
                continue

        except Exception as e:
            restart_count += 1
            failure_times.append(time.monotonic() - global_start)
            ts  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            msg = f"\n  ⚠ [{ts}] Crash detected (#{restart_count}): {e}"
            print(msg)
            if log_file:
                log_file.write(msg + "\n")
            time.sleep(0.5)
            continue

    elapsed    = time.monotonic() - global_start
    throughput = total_count / elapsed if elapsed > 0 else 0

    # -- MTBF ------------------------------------------------------------------
    if len(failure_times) >= 2:
        gaps = [failure_times[i+1] - failure_times[i] for i in range(len(failure_times)-1)]
        mtbf = sum(gaps) / len(gaps)
    elif len(failure_times) == 1:
        mtbf = failure_times[0]
    else:
        mtbf = None

    # -- Summary ---------------------------------------------------------------
    print(f"\n  Total readings  : {total_count}")
    print(f"  Restarts        : {restart_count}")
    print(f"  Duration        : {elapsed:.2f}s")
    print(f"  Throughput      : {throughput:.2f} reads/sec")

    if log_file:
        log_file.write(f"\nTotal readings   : {total_count}\n")
        log_file.write(f"Restarts         : {restart_count}\n")
        log_file.write(f"Duration         : {elapsed:.2f}s\n")
        log_file.write(f"Throughput       : {throughput:.2f} reads/sec\n")

    if mtbf is not None:
        mtbf_line = f"  MTBF            : {mtbf:.2f}s"
        print(mtbf_line)
        if log_file:
            log_file.write(mtbf_line.lstrip() + "\n")
    else:
        print("  MTBF            : No failures detected")
        if log_file:
            log_file.write("MTBF            : No failures detected\n")

    sen_stats = _sensor_stats(temp_values)
    _print_sensor_stats(sen_stats, log_file)

    lat_stats = _latency_stats(intervals)
    _print_latency_stats(lat_stats, log_file)

    res_stats = _resource_stats(all_mem_samples)
    _print_resource_stats(res_stats, log_file)

    return total_count > 0


def build_and_run_c(timeout: int, log: bool, hz: float) -> bool:
    print(f"\n{'─' * 50}")
    print("  C Driver — compile + run")
    print(f"{'─' * 50}")
    print(hz)

    log_file = None
    if log:
        log_path = SCRIPT_DIR / "Logs" / "Reliability" / "C" / f"c_driver_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        log_file = open(log_path, "w")
        print(f"  Logging to: {log_path.name}")

    try:
        ok = run_step(
            "gcc compile",
            [
                "gcc", "-std=c11", "-D_DEFAULT_SOURCE",
                "-Wall", "-Wextra", "-O2",
                "main.c", "bme280.c", "i2c_linux.c",
                "-o", C_BINARY,
            ],
            cwd=C_DIR,
            timeout=COMPILE_TIMEOUT,
            log_file=log_file,
        )
        if not ok:
            return False

        cmd = [f"./{C_BINARY}"]
        cmd.append("0x76")
        if hz > 0:
            cmd.append(str(hz))

        ok = capture_temperature_readings(
            "run",
            cmd,
            cwd=C_DIR,
            timeout=timeout,
            log_file=log_file,
        )

        print(f"\n  {'PASSED' if ok else 'FAILED'}")
        return ok

    finally:
        if log_file:
            log_file.close()


def build_and_run_rust(timeout: int, log: bool, hz: float) -> bool:
    print(f"\n{'─' * 50}")
    print("  Rust Driver — cargo build + cargo run")
    print(f"{'─' * 50}")

    log_file = None
    if log:
        log_path = SCRIPT_DIR / "Logs" / "Reliability" / "Rust" / f"rust_driver_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        log_file = open(log_path, "w")
        print(f"  Logging to: {log_path.name}")

    try:
        ok = run_step(
            "cargo build",
            ["cargo", "build", "--release"],
            cwd=RUST_DIR,
            timeout=COMPILE_TIMEOUT,
            log_file=log_file,
        )
        if not ok:
            return False

        run_args = ["cargo", "run", "--release", "--", "0x76", str(hz)]

        ok = capture_temperature_readings(
            "cargo run",
            run_args,
            cwd=RUST_DIR,
            timeout=timeout,
            log_file=log_file,
        )

        print(f"\n  {'PASSED' if ok else 'FAILED'}")
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
        "--timeout", type=int, default=60, metavar="SECONDS",
        help="How long to collect readings in seconds (default: 60)",
    )
    parser.add_argument(
        "--log", action="store_true", default=True,
        help="Save each driver's output to a timestamped .log file in Python_tests/",
    )
    parser.add_argument(
        "--parallel", action="store_true",
        help="Alternate C/Rust readings, compare results at end",
    )

    parser.add_argument(
        "--hz", type=float, default=1,
        help="Driver frequency",
    )

    args = parser.parse_args()
    if args.parallel:
        args.both = True
        args.c    = False
        args.rust = False
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

        c_temps    = []
        rust_temps = []
        timeout    = args.timeout
        start      = time.monotonic()
        read_index = 0

        def get_temp_from_line(line):
            import re
            m = re.search(r"Temperature:\s*([-+]?[0-9]*\.?[0-9]+)", line)
            if m:
                return float(m.group(1))
            return None

        while time.monotonic() - start < timeout:
            read_index += 1
            if read_index % 2 == 1:
                label = f"C_read_{read_index}"
                cmd   = [f"./{C_BINARY}"]
                cwd   = C_DIR
            else:
                label = f"Rust_read_{read_index}"
                cmd   = ["cargo", "run", "--release"]
                cwd   = RUST_DIR

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
                        print(f"  [{read_index}] {label}: {line.strip()}")
                        break
                proc.kill()
                proc.wait()
                if temp_val is not None:
                    if read_index % 2 == 1:
                        c_temps.append(temp_val)
                    else:
                        rust_temps.append(temp_val)
                else:
                    print(f"  x No temperature found for {label}")
            except Exception as e:
                print(f"  x Error running {label}: {e}")

        print(f"\n{'=' * 50}")
        print("  Comparison of Sensor Readings")
        print(f"{'=' * 50}")
        min_len = min(len(c_temps), len(rust_temps))
        diffs   = []
        for idx in range(min_len):
            diff = abs(c_temps[idx] - rust_temps[idx])
            diffs.append(diff)
            print(f"  Pair {idx+1}: C={c_temps[idx]:.2f}  Rust={rust_temps[idx]:.2f}  |d|={diff:.4f}")
        if diffs:
            print(f"\n  Average |d|: {sum(diffs)/len(diffs):.4f}")
            print(f"  Max |d|: {max(diffs):.4f}")
        else:
            print("  No valid pairs to compare.")
        print(f"{'=' * 50}\n")
        sys.exit(0)

    results: dict[str, bool] = {}

    if args.c or args.both:
        results["C"] = build_and_run_c(args.timeout, args.log, args.hz)

    if args.rust or args.both:
        results["Rust"] = build_and_run_rust(args.timeout, args.log, args.hz)

    print(f"\n{'=' * 50}")
    print("  Summary")
    print(f"{'=' * 50}")
    all_passed = True
    for driver, passed in results.items():
        icon = "+" if passed else "x"
        print(f"  {icon}  {driver} driver — {'PASSED' if passed else 'FAILED'}")
        if not passed:
            all_passed = False

    print(f"{'=' * 50}\n")
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
