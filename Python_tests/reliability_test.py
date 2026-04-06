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
    # Drop the first reading's interval to filter out cold-start/initialization lags
    if len(intervals) > 2:
        intervals = intervals[1:]

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
        print("  Parallel Mode: Concurrent C/Rust reading")
        print(f"{'─' * 50}")

        print("Compiling drivers...")
        run_step("gcc compile", ["gcc", "-std=c11", "-D_DEFAULT_SOURCE", "-Wall", "-Wextra", "-O2", "main.c", "bme280.c", "i2c_linux.c", "-o", C_BINARY], cwd=C_DIR, timeout=COMPILE_TIMEOUT, log_file=None)
        run_step("cargo build", ["cargo", "build", "--release"], cwd=RUST_DIR, timeout=COMPILE_TIMEOUT, log_file=None)
        
        c_cmd = [f"./{C_BINARY}", "0x76", str(args.hz), "/dev/i2c-1", "--coord-lock"]
        # Call the Rust binary directly to skip 'cargo run' memory overhead and startup lag
        rust_cmd = [str(RUST_DIR / "target" / "release" / "bme280_bare_bones"), "0x76", str(args.hz), "/dev/i2c-1", "--coord-lock"]

        c_samples = []
        rust_samples = []
        c_intervals = []
        rust_intervals = []
        
        c_last_ts = [None]
        rust_last_ts = [None]

        samples_lock = threading.Lock()
        stop_event = threading.Event()
        record_event = threading.Event()
        c_ready_event = threading.Event()
        rust_ready_event = threading.Event()

        log_file = None
        if args.log:
            log_path = SCRIPT_DIR / "Logs" / "Reliability" / "parallel" / f"parallel_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_file = open(log_path, "w")

        print("\nStarting parallel temperature capture...")
        
        c_proc = subprocess.Popen([str(c) for c in c_cmd], cwd=str(C_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        time.sleep(0.1) # stagger
        rust_proc = subprocess.Popen([str(c) for c in rust_cmd], cwd=str(RUST_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        
        c_sampler = ResourceSampler(c_proc.pid)
        rust_sampler = ResourceSampler(rust_proc.pid)
        c_sampler.start()
        rust_sampler.start()

        def reader_thread(label, proc, samples_list, intervals_list, last_ts_ref, ready_ev):
            while not stop_event.is_set():
                line = proc.stdout.readline()
                if not line:
                    break
                line = line.rstrip()
                
                if "initialized successfully" in line:
                    ready_ev.set()

                if TEMP_PATTERN in line:
                    ready_ev.set() # Fallback flag
                    if record_event.is_set():
                        now = time.monotonic()
                        m = _re.search(r"Temperature:\s*([-+]?[0-9]*\.?[0-9]+)", line)
                        if m:
                            temp = float(m.group(1))
                            with samples_lock:
                                samples_list.append((now, temp))
                                if last_ts_ref[0] is not None:
                                    intervals_list.append(now - last_ts_ref[0])
                                last_ts_ref[0] = now
                            out = f"{label} [{datetime.now().isoformat()}]: {line}"
                            print(f"  {out}")
                            if log_file:
                                log_file.write(out + "\n")
                                log_file.flush()
                else:
                    out = f"{label}: {line}"
                    print(f"  {out}")
                    if log_file:
                        log_file.write(out + "\n")
                        log_file.flush()

        ct = threading.Thread(target=reader_thread, args=("C_driver", c_proc, c_samples, c_intervals, c_last_ts, c_ready_event), daemon=True)
        rt = threading.Thread(target=reader_thread, args=("Rust_driver", rust_proc, rust_samples, rust_intervals, rust_last_ts, rust_ready_event), daemon=True)
        
        ct.start()
        rt.start()

        print("\n  Waiting for both drivers to initialize...")
        c_ready_event.wait(timeout=10)
        rust_ready_event.wait(timeout=10)
        print("  Both drivers ready! Starting metric collection...")

        start_time = time.monotonic()
        record_event.set()

        c_restarts = 0
        rust_restarts = 0
        try:
            while time.monotonic() - start_time < args.timeout:
                time.sleep(0.1)
                if c_proc.poll() is not None:
                    print(f"\n  ⚠ [C_driver] crashed! Restarting...")
                    if log_file: log_file.write(f"\n  ⚠ [C_driver] crashed! Restarting...\n")
                    c_restarts += 1
                    c_sampler.stop()
                    c_proc = subprocess.Popen([str(c) for c in c_cmd], cwd=str(C_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                    c_sampler = ResourceSampler(c_proc.pid)
                    c_sampler.start()
                    ct = threading.Thread(target=reader_thread, args=("C_driver", c_proc, c_samples, c_intervals, c_last_ts, c_ready_event), daemon=True)
                    ct.start()
                    
                if rust_proc.poll() is not None:
                    print(f"\n  ⚠ [Rust_driver] crashed! Restarting...")
                    if log_file: log_file.write(f"\n  ⚠ [Rust_driver] crashed! Restarting...\n")
                    rust_restarts += 1
                    rust_sampler.stop()
                    rust_proc = subprocess.Popen([str(c) for c in rust_cmd], cwd=str(RUST_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                    rust_sampler = ResourceSampler(rust_proc.pid)
                    rust_sampler.start()
                    rt = threading.Thread(target=reader_thread, args=("Rust_driver", rust_proc, rust_samples, rust_intervals, rust_last_ts, rust_ready_event), daemon=True)
                    rt.start()
        except KeyboardInterrupt:
            print("\nInterrupted.")
            
        elapsed = time.monotonic() - start_time
        stop_event.set()
        c_sampler.stop()
        rust_sampler.stop()
        
        c_proc.terminate()
        rust_proc.terminate()
        try:
            c_proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            c_proc.kill()
        try:
            rust_proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            rust_proc.kill()
        
        with samples_lock:
            c_vals = [s[1] for s in c_samples]
            r_vals = [s[1] for s in rust_samples]
            
        c_throughput = len(c_vals) / elapsed if elapsed > 0 else 0
        r_throughput = len(r_vals) / elapsed if elapsed > 0 else 0

        print(f"\n{'=' * 50}")
        print("  C Driver Metrics")
        print(f"{'=' * 50}")
        if log_file:
            log_file.write(f"\n{'=' * 50}\n  C Driver Metrics\n{'=' * 50}\n")
        
        c_summary = (f"Total readings   : {len(c_vals)}\n"
                     f"Restarts         : {c_restarts}\n"
                     f"Duration         : {elapsed:.2f}s\n"
                     f"Throughput       : {c_throughput:.2f} reads/sec\n"
                     f"MTBF             : {elapsed / (c_restarts + 1):.2f}s\n")
        print(c_summary, end="")
        if log_file:
            log_file.write(c_summary)
            
        _print_sensor_stats(_sensor_stats(c_vals), log_file)
        _print_latency_stats(_latency_stats(c_intervals), log_file)
        _print_resource_stats(_resource_stats(c_sampler.mem_samples), log_file)

        print(f"\n{'=' * 50}")
        print("  Rust Driver Metrics")
        print(f"{'=' * 50}")
        if log_file:
            log_file.write(f"\n{'=' * 50}\n  Rust Driver Metrics\n{'=' * 50}\n")
        
        r_summary = (f"Total readings   : {len(r_vals)}\n"
                     f"Restarts         : {rust_restarts}\n"
                     f"Duration         : {elapsed:.2f}s\n"
                     f"Throughput       : {r_throughput:.2f} reads/sec\n"
                     f"MTBF             : {elapsed / (rust_restarts + 1):.2f}s\n")
        print(r_summary, end="")
        if log_file:
            log_file.write(r_summary)
            
        _print_sensor_stats(_sensor_stats(r_vals), log_file)
        _print_latency_stats(_latency_stats(rust_intervals), log_file)
        _print_resource_stats(_resource_stats(rust_sampler.mem_samples), log_file)

        print(f"\n{'=' * 50}")
        print("  Comparison / Delta Metrics")
        print(f"{'=' * 50}")
        if log_file:
            log_file.write(f"\n{'=' * 50}\n  Comparison / Delta Metrics\n{'=' * 50}\n")
        
        def _local_pair_by_time(c_s, rust_s, max_gap_s):
            pairs = []
            i = j = 0
            while i < len(c_s) and j < len(rust_s):
                c_ts, c_tmp = c_s[i]
                r_ts, r_tmp = rust_s[j]
                dt = r_ts - c_ts
                if abs(dt) <= max_gap_s:
                    pairs.append((c_ts, c_tmp, r_ts, r_tmp, abs(c_tmp - r_tmp)))
                    i += 1
                    j += 1
                elif dt < 0:
                    j += 1
                else:
                    i += 1
            return pairs

        # Increased the gap limit slightly from 0.2 to 0.4s to account for minor loop offsets
        pairs = _local_pair_by_time(c_samples, rust_samples, 0.4)
        deltas = [p[4] for p in pairs]
        
        print(f"  Paired readings (gap <= 0.4s): {len(pairs)}")
        if deltas:
            d_mean = sum(deltas)/len(deltas)
            d_max = max(deltas)
            print(f"  Average |C - Rust|: {d_mean:.4f} °C")
            print(f"  Max |C - Rust|    : {d_max:.4f} °C")
            if log_file:
                log_file.write(f"\nPaired readings: {len(pairs)}\nAverage |C - Rust|: {d_mean:.4f} C\nMax |C - Rust|: {d_max:.4f} C\n")
        else:
            print("  No valid pairs to compare.")
            if log_file:
                log_file.write("No valid pairs to compare.\n")
        
        print(f"{'=' * 50}\n")
        if log_file:
            log_file.close()
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
