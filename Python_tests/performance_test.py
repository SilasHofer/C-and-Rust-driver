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


TEMP_SAMPLES    = 1000
TEMP_PATTERN    = "Temperature:"
LOG_EVERY_N     = 10


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


def _resource_stats(cpu_samples: list[float], mem_samples: list[float]) -> dict:
    """
    Summarise CPU (%) and RSS memory (bytes) collected during a run.
    """
    valid_cpu = cpu_samples
    n_cpu = len(valid_cpu)
    n_mem = len(mem_samples)
    if n_cpu == 0 and n_mem == 0:
        return {}

    result = {}
    if n_cpu:
        result["cpu_mean"]     = sum(valid_cpu) / n_cpu
        result["cpu_peak"]     = max(valid_cpu)
        result["cpu_n"]        = n_cpu
        result["cpu_skipped"]  = len(cpu_samples) - n_cpu
    if n_mem:
        result["mem_mean_kb"]  = (sum(mem_samples) / n_mem) / 1024
        result["mem_peak_kb"]  = max(mem_samples) / 1024
        result["mem_n"]        = n_mem
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
    if "cpu_mean" in stats:
        skipped = stats.get("cpu_skipped", 0)
        skip_note = f"  ({skipped} zero samples excluded)" if skipped else ""
        lines += [
            f"    CPU mean    : {stats['cpu_mean']:6.1f} %  (n={stats['cpu_n']}{skip_note})",
            f"    CPU peak    : {stats['cpu_peak']:6.1f} %",
        ]
    if "mem_mean_kb" in stats:
        lines += [
            f"    Mem mean    : {stats['mem_mean_kb']:8.1f} KB",
            f"    Mem peak    : {stats['mem_peak_kb']:8.1f} KB",
        ]
    for line in lines:
        print(line)
        if log_file:
            log_file.write(line.lstrip() + "\n")

def _print_cpu_time_stats(cpu_time: float, wall_time: float, log_file) -> None:
    """
    Print total CPU time consumed and CPU efficiency.
    """
    if wall_time <= 0:
        return

    efficiency = (cpu_time / wall_time) * 100.0

    lines = [
        "  CPU time (process accounting):",
        f"    CPU time     : {cpu_time:8.3f} s",
        f"    CPU efficiency: {efficiency:8.2f} %",
    ]

    for line in lines:
        print(line)
        if log_file:
            log_file.write(line.lstrip() + "\n")

class ResourceSampler:
    def __init__(self, pid: int, interval: float = 0.02):
        self.proc = psutil.Process(pid)
        self.interval = interval

        self.cpu_samples = []
        self.mem_samples = []

        self._running = False
        self._thread = None

    def start(self):
        self._running = True

        # prime cpu measurement
        self.proc.cpu_percent(None)

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join()

    def _run(self):
        while self._running:
            try:
                cpu = self.proc.cpu_percent(None)
                mem = self.proc.memory_info().rss

                self.cpu_samples.append(cpu)
                self.mem_samples.append(mem)

            except psutil.NoSuchProcess:
                break

            time.sleep(self.interval)


def capture_temperature_readings(
    label: str, cmd: list, cwd: Path, timeout: int, log_file, samples: int
) -> bool:
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

    count       = 0
    ok          = False
    intervals   = []    # inter-reading latencies (seconds)
    last_ts     = None
    cpu_samples = []    # per-reading CPU % snapshots
    mem_samples = []    # per-reading RSS bytes snapshots

    try:
        proc = subprocess.Popen(
            [str(c) for c in cmd],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )


        ps_proc = psutil.Process(proc.pid)
        cpu_start = ps_proc.cpu_times()

        sampler = ResourceSampler(proc.pid)
        sampler.start()

        for line in proc.stdout:
            line = line.rstrip()

            if TEMP_PATTERN in line:
                now = time.monotonic()
                reading_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]  # millisecond precision

                # Latency
                if last_ts is not None:
                    intervals.append(now - last_ts)
                last_ts = now

                count += 1
                print(f"  [{count}/{samples}] [{reading_ts}] {line}")

                if log_file and count % LOG_EVERY_N == 0:
                    log_file.write(
                        f"[{count}/{samples}] [{reading_ts}] {line}\n"
                    )

            else:
                print(f"  {line}")
                if log_file:
                    log_file.write(line + "\n")

            if count >= samples:
                ok = True
                break

            if time.monotonic() - start > timeout:
                print(f"\n  x Timed out after {timeout}s ({count}/{samples} readings)")
                if log_file:
                    log_file.write(f"\nTimed out after {timeout}s ({count}/{samples} readings)\n")
                break

    except FileNotFoundError as e:
        print(f"  x Command not found: {e}")
        if log_file:
            log_file.write(f"Command not found: {e}\n")
        return False

    finally:
        cpu_end = None
        try:
            cpu_end = ps_proc.cpu_times()
        except Exception:
            pass

        try:
            sampler.stop()
            proc.kill()
            proc.wait()
        except Exception:
            pass

    end_dt  = datetime.now()
    elapsed = time.monotonic() - start

    cpu_time_total = None
    if cpu_end:
        cpu_time_total = (
            (cpu_end.user - cpu_start.user) +
            (cpu_end.system - cpu_start.system)
        )

    throughput = count / elapsed if elapsed > 0 else 0

    print(f"\n  End time   : {end_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Readings   : {count}/{samples}")
    print(f"  Duration   : {elapsed:.2f}s")
    print(f"  Throughput : {throughput:.2f} reads/sec")

    if log_file:
        log_file.write(f"\nEnd time   : {end_dt.strftime('%Y-%m-%d %H:%M:%S')}\n")
        log_file.write(f"Readings   : {count}/{samples}\n")
        log_file.write(f"Duration   : {elapsed:.2f}s\n")
        log_file.write(f"Throughput : {throughput:.2f} reads/sec\n")

    lat_stats = _latency_stats(intervals)
    _print_latency_stats(lat_stats, log_file)

    if cpu_time_total is not None:
        _print_cpu_time_stats(cpu_time_total, elapsed, log_file)

    res_stats = _resource_stats(
        sampler.cpu_samples,
        sampler.mem_samples
    )
    _print_resource_stats(res_stats, log_file)

    return ok


def build_and_run_c(timeout: int, log: bool, samples: int) -> bool:
    print(f"\n{'─' * 50}")
    print("  C Driver — compile + run")
    print(f"{'─' * 50}")

    log_file = None
    if log:
        log_path = SCRIPT_DIR / "Logs" / "Performance" / "C" / f"c_driver_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
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
            timeout=timeout,
            log_file=log_file,
        )
        if not ok:
            return False

        cmd = [f"./{C_BINARY}"]
        cmd.append("0x76")
        cmd.append(0)

        ok = capture_temperature_readings(
            "run",
            cmd,
            cwd=C_DIR,
            timeout=timeout,
            log_file=log_file,
            samples=samples,
        )

        print(f"\n  {'PASSED' if ok else 'FAILED'}")
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
        log_path = SCRIPT_DIR / "Logs" / "Performance" / "Rust" / f"rust_driver_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        log_file = open(log_path, "w")
        print(f"  Logging to: {log_path.name}")

    try:
        ok = run_step(
            "cargo build",
            ["cargo", "build", "--release"],
            cwd=RUST_DIR,
            timeout=timeout,
            log_file=log_file,
        )
        if not ok:
            return False

        run_args = [str(RUST_DIR / "target" / "release" / "bme280_bare_bones"), "0x76", "0"]

        ok = capture_temperature_readings(
            "rust binary",
            run_args,
            cwd=RUST_DIR,
            timeout=timeout,
            log_file=log_file,
            samples=samples,
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
        "--samples", type=int, default=TEMP_SAMPLES, metavar="N",
        help=f"Number of temperature readings to capture (default: {TEMP_SAMPLES})",
    )
    parser.add_argument(
        "--timeout", type=int, default=60, metavar="SECONDS",
        help="Per-step timeout in seconds (default: 60)",
    )
    parser.add_argument(
        "--log", action="store_true",
        help="Save each driver's output to a timestamped .log file in Python_tests/",
    )
    parser.add_argument(
        "--parallel", action="store_true",
        help="Alternate C/Rust readings, compare results at end",
    )

    args = parser.parse_args()
    if args.parallel:
        args.both = True
        args.c = False
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

        c_temps = []
        rust_temps = []
        total_reads = args.samples
        timeout = args.timeout

        def get_temp_from_line(line):
            import re
            m = re.search(r"Temperature:\s*([-+]?[0-9]*\.?[0-9]+)", line)
            if m:
                return float(m.group(1))
            return None

        for i in range(1, total_reads + 1):
            if i % 2 == 1:
                label = f"C_read_{i}"
                cmd = [f"./{C_BINARY}"]
                cwd = C_DIR
            else:
                label = f"Rust_read_{i}"
                cmd = [str(RUST_DIR / "target" / "release" / "bme280_bare_bones")]
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
                    print(f"  x No temperature found for {label}")
            except Exception as e:
                print(f"  x Error running {label}: {e}")

        print(f"\n{'=' * 50}")
        print("  Comparison of Sensor Readings")
        print(f"{'=' * 50}")
        min_len = min(len(c_temps), len(rust_temps))
        diffs = []
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
        results["C"] = build_and_run_c(args.timeout, args.log, args.samples)

    if args.rust or args.both:
        results["Rust"] = build_and_run_rust(args.timeout, args.log, args.samples)

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