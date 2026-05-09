#!/usr/bin/env python3
"""
Driver Test Runner for Raspberry Pi 3
Compiles and runs the C driver, Rust driver, or both.

Assumed folder layout (relative to this script's location):
  ../C_Driver/ <- C source files
  ../Rust_Driver/         <- Rust project (has Cargo.toml)

Requires passwordless sudo for:
  - /usr/bin/tee (writing to /proc/sys/vm/drop_caches and scaling_governor)
  - /bin/sync

Add to /etc/sudoers via `sudo visudo -f /etc/sudoers.d/pi-benchmark`:
  pi ALL=(ALL) NOPASSWD: /usr/bin/tee, /bin/sync
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

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent.resolve()
ROOT_DIR = SCRIPT_DIR.parent
C_DIR = ROOT_DIR / "C_Driver"
C_BINARY = "c_driver"
RUST_DIR = ROOT_DIR / "Rust_Driver"
RUST_BINARY = RUST_DIR / "target" / "release" / "bme280_bare_bones"

# ─────────────────────────────────────────────────────────────────────────────
TEMP_SAMPLES = 1000
TEMP_PATTERN = "Temperature:"
LOG_EVERY_N = 1
WARMUP_READS = 100          # warm-up readings excluded from all performance metrics
SETTLE_SECONDS = 2          # sleep after cleanup before the driver starts
CPU_CORES = 4               # Raspberry Pi 3 has 4 cores

# ── System cleanup ────────────────────────────────────────────────────────────
def system_cleanup(log_file) -> None:
    """
    Bring the system to a consistent baseline before each driver run.
    Steps
    -----
    1. Kill any leftover driver processes from a previous run.
    2. Flush dirty pages to disk (sync).
    3. Drop page cache, dentry cache and inode cache.
    4. Pin all CPU cores to the 'performance' governor (fixed clock speed).
    5. Sleep briefly so the scheduler and I2C bus can settle.
    """
    _cprint("\n [cleanup] Starting system cleanup...", log_file)
    _kill_leftover_drivers(log_file)
    _sync_and_drop_caches(log_file)
    _set_cpu_governor("performance", log_file)
    _cprint(f" [cleanup] Settling for {SETTLE_SECONDS}s...", log_file)
    time.sleep(SETTLE_SECONDS)
    _cprint(" [cleanup] Done — system is clean.\n", log_file)


def _cprint(msg: str, log_file) -> None:
    """Print a cleanup message and optionally write it to the log."""
    print(msg)
    if log_file:
        log_file.write(msg.strip() + "\n")


def _kill_leftover_drivers(log_file) -> None:
    """Kill any running instances of the C or Rust driver binary."""
    targets = [C_BINARY, "bme280_bare_bones"]
    killed = []
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            name = proc.info["name"] or ""
            exe = proc.info["exe"] or ""
            if any(t in name or t in exe for t in targets):
                proc.kill()
                proc.wait(timeout=3)
                killed.append(f"{name}(pid={proc.pid})")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if killed:
        _cprint(f" [cleanup] Killed leftover processes: {', '.join(killed)}", log_file)
    else:
        _cprint(" [cleanup] No leftover driver processes found.", log_file)


def _sync_and_drop_caches(log_file) -> None:
    """Flush dirty pages to disk, then drop page / dentry / inode caches."""
    # Flush dirty pages
    try:
        subprocess.run(["sync"], check=True)
        _cprint(" [cleanup] sync: OK", log_file)
    except Exception as e:
        _cprint(f" [cleanup] sync failed: {e}", log_file)

    # Drop caches
    try:
        subprocess.run(
            ["sudo", "tee", "/proc/sys/vm/drop_caches"],
            input="3",
            text=True,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        _cprint(" [cleanup] drop_caches (3): OK", log_file)
    except subprocess.CalledProcessError as e:
        _cprint(
            f" [cleanup] drop_caches failed (returncode={e.returncode}). "
            "Is `sudo tee` allowed without a password? See the sudoers note at "
            "the top of this file.",
            log_file,
        )
    except FileNotFoundError:
        _cprint(" [cleanup] drop_caches failed: `sudo` not found.", log_file)


def _set_cpu_governor(governor: str, log_file) -> None:
    """
    Pin every CPU core to the given governor (e.g. 'performance')
    using the modern sysfs interface — no extra packages needed.
    """
    _cprint(f" [cleanup] Setting CPU governor to '{governor}' on all {CPU_CORES} cores...", log_file)
    failed = []
    for core in range(CPU_CORES):
        path = f"/sys/devices/system/cpu/cpu{core}/cpufreq/scaling_governor"
        try:
            subprocess.run(
                ["sudo", "tee", path],
                input=governor,
                text=True,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError:
            failed.append(core)
        except FileNotFoundError:
            _cprint(" [cleanup] Governor sysfs path not found — skipping.", log_file)
            return
    if failed:
        _cprint(f" [cleanup] Governor set failed for cores {failed}.", log_file)
    else:
        _cprint(f" [cleanup] CPU governor successfully set to '{governor}' on all cores.", log_file)


# ── Helpers ───────────────────────────────────────────────────────────────────
def run_step(label: str, cmd: list, cwd: Path, timeout: int, log_file) -> bool:
    """Run a shell command, stream output, return True on success."""
    print(f"\n $ {' '.join(str(c) for c in cmd)}")
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
            print(f" x '{label}' exited with code {result.returncode}")
            return False
        return True
    except FileNotFoundError as e:
        msg = f" x Command not found: {e}"
        print(msg)
        if log_file:
            log_file.write(msg + "\n")
        return False
    except subprocess.TimeoutExpired:
        msg = f" x '{label}' timed out after {timeout}s"
        print(msg)
        if log_file:
            log_file.write(msg + "\n")
        return False


def _latency_stats(intervals: list[float]) -> dict:
    n = len(intervals)
    if n == 0:
        return {}
    mean = sum(intervals) / n
    variance = sum((x - mean) ** 2 for x in intervals) / n
    return {
        "n": n,
        "mean_ms": mean * 1000,
        "std_ms": math.sqrt(variance) * 1000,
        "worst_ms": max(intervals) * 1000,
    }


def _resource_stats(cpu_samples: list[float], mem_samples: list[float]) -> dict:
    n_cpu = len(cpu_samples)
    n_mem = len(mem_samples)
    if n_cpu == 0 and n_mem == 0:
        return {}
    result = {}
    if n_cpu:
        result["cpu_mean"] = sum(cpu_samples) / n_cpu
        result["cpu_peak"] = max(cpu_samples)
        result["cpu_n"] = n_cpu
        result["cpu_skipped"] = 0
    if n_mem:
        result["mem_mean_kb"] = (sum(mem_samples) / n_mem) / 1024
        result["mem_peak_kb"] = max(mem_samples) / 1024
        result["mem_n"] = n_mem
    return result


def _print_latency_stats(stats: dict, log_file) -> None:
    if not stats:
        return
    lines = [
        f" Latency (inter-reading interval, n={stats['n']}):",
        f" Mean : {stats['mean_ms']:8.3f} ms",
        f" Std-dev : {stats['std_ms']:8.3f} ms",
        f" Worst-case : {stats['worst_ms']:8.3f} ms",
    ]
    for line in lines:
        print(line)
        if log_file:
            log_file.write(line.lstrip() + "\n")


def _print_resource_stats(stats: dict, log_file) -> None:
    if not stats:
        return
    lines = [" Resource usage (driver process):"]
    if "cpu_mean" in stats:
        skipped = stats.get("cpu_skipped", 0)
        skip_note = f" ({skipped} zero samples excluded)" if skipped else ""
        lines += [
            f" CPU mean : {stats['cpu_mean']:6.1f} % (n={stats['cpu_n']}{skip_note})",
            f" CPU peak : {stats['cpu_peak']:6.1f} %",
        ]
    if "mem_mean_kb" in stats:
        lines += [
            f" Mem mean : {stats['mem_mean_kb']:8.1f} KB",
            f" Mem peak : {stats['mem_peak_kb']:8.1f} KB",
        ]
    for line in lines:
        print(line)
        if log_file:
            log_file.write(line.lstrip() + "\n")


def _print_cpu_time_stats(cpu_time: float, wall_time: float, log_file) -> None:
    if wall_time <= 0:
        return
    efficiency = (cpu_time / wall_time) * 100.0
    lines = [
        " CPU time (process accounting):",
        f" CPU time : {cpu_time:8.3f} s",
        f" CPU efficiency: {efficiency:8.2f} %",
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
        self.proc.cpu_percent(None)  # prime the measurement
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join()

    def _run(self):
        while self._running:
            try:
                self.cpu_samples.append(self.proc.cpu_percent(None))
                self.mem_samples.append(self.proc.memory_info().rss)
            except psutil.NoSuchProcess:
                break
            time.sleep(self.interval)


# ── Core measurement loop ─────────────────────────────────────────────────────
def capture_temperature_readings(
    label: str, cmd: list, cwd: Path, timeout: int, log_file,
    samples: int, warmup: int = WARMUP_READS,
) -> bool:
    """
    Launch a driver process, discard `warmup` temperature lines (warm-up phase),
    then capture `samples` temperature lines for performance measurement.
    """
    import select
    print(f"\n $ {' '.join(str(c) for c in cmd)}")
    print(f" Warm-up : {warmup} readings (excluded from metrics)")
    print(f" Measuring : {samples} readings\n")

    if log_file:
        log_file.write(f"[{label}]\n")
        log_file.write(f"Warm-up : {warmup} readings (excluded from metrics)\n")
        log_file.write(f"Target : {samples} readings (logging every {LOG_EVERY_N})\n\n")

    warmup_done = False
    warmup_count = 0
    count = 0
    ok = False
    intervals = []
    last_ts = None
    start_dt = None
    start = None
    cpu_start = None
    sampler = None
    ps_proc = None

    try:
        proc = subprocess.Popen(
            [str(c) for c in cmd],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        ps_proc = psutil.Process(proc.pid)
        last_activity = time.monotonic()

        while True:
            ready, _, _ = select.select([proc.stdout], [], [], 0.5)
            if not ready:
                if time.monotonic() - last_activity > timeout:
                    phase = "warm-up" if not warmup_done else "measurement"
                    progress = (
                        f"{warmup_count}/{warmup}" if not warmup_done
                        else f"{count}/{samples}"
                    )
                    print(
                        f"\n x Timed out during {phase}: "
                        f"no output for {timeout}s ({progress} readings)"
                    )
                    if log_file:
                        log_file.write(
                            f"\nTimed out during {phase}: "
                            f"no output for {timeout}s ({progress} readings)\n"
                        )
                    break
                continue

            line = proc.stdout.readline()
            if not line:
                break
            last_activity = time.monotonic()
            line = line.rstrip()

            if TEMP_PATTERN not in line:
                if warmup_done:
                    print(f" {line}")
                    if log_file:
                        log_file.write(line + "\n")
                continue

            # ── Warm-up phase ──────────────────────────────────────────────
            if not warmup_done:
                warmup_count += 1
                print(f" [warm-up {warmup_count}/{warmup}] {line}")
                if warmup_count >= warmup:
                    warmup_done = True
                    start_dt = datetime.now()
                    start = time.monotonic()
                    cpu_start = ps_proc.cpu_times()
                    sampler = ResourceSampler(proc.pid)
                    sampler.start()
                    print("\n Warm-up complete — starting performance measurement")
                    print(f" Start time : {start_dt.strftime('%Y-%m-%d %H:%M:%S')}\n")
                    if log_file:
                        log_file.write("\nWarm-up complete\n")
                        log_file.write(
                            f"Start time : {start_dt.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                        )
                continue

            # ── Measurement phase ──────────────────────────────────────────
            now = time.monotonic()
            reading_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
            if last_ts is not None:
                intervals.append(now - last_ts)
            last_ts = now
            count += 1
            print(f" [{count}/{samples}] [{reading_ts}] {line}")

            if log_file and count % LOG_EVERY_N == 0:
                try:
                    snap_mem_kb = ps_proc.memory_info().rss / 1024
                    snap_cpu = ps_proc.cpu_times()
                    snap_cpu_s = (
                        (snap_cpu.user + snap_cpu.system)
                        - (cpu_start.user + cpu_start.system)
                    )
                except psutil.NoSuchProcess:
                    snap_mem_kb = 0.0
                    snap_cpu_s = 0.0
                latency_str = (
                    f" lat={intervals[-1] * 1000:.3f}ms" if intervals else " lat=N/A"
                )
                log_file.write(
                    f"[{count}/{samples}] [{reading_ts}] {line}"
                    f" | mem={snap_mem_kb:.1f}KB cpu={snap_cpu_s:.3f}s{latency_str}\n"
                )

            if count >= samples:
                ok = True
                break

    except FileNotFoundError as e:
        print(f" x Command not found: {e}")
        if log_file:
            log_file.write(f"Command not found: {e}\n")
        return False
    finally:
        cpu_end = None
        if ps_proc is not None:
            try:
                cpu_end = ps_proc.cpu_times()
            except Exception:
                pass
        if sampler is not None:
            try:
                sampler.stop()
            except Exception:
                pass
        try:
            proc.kill()
            proc.wait()
        except Exception:
            pass

    if not warmup_done or start is None:
        print("\n x Warm-up phase did not complete — no performance metrics recorded.")
        return False

    end_dt = datetime.now()
    elapsed = time.monotonic() - start
    cpu_time_total = None
    if cpu_end and cpu_start:
        cpu_time_total = (
            (cpu_end.user - cpu_start.user) +
            (cpu_end.system - cpu_start.system)
        )

    throughput = count / elapsed if elapsed > 0 else 0
    print(f"\n End time : {end_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f" Readings : {count}/{samples}")
    print(f" Duration : {elapsed:.2f}s")
    print(f" Throughput : {throughput:.2f} reads/sec")

    if log_file:
        log_file.write(f"\nEnd time : {end_dt.strftime('%Y-%m-%d %H:%M:%S')}\n")
        log_file.write(f"Readings : {count}/{samples}\n")
        log_file.write(f"Duration : {elapsed:.2f}s\n")
        log_file.write(f"Throughput : {throughput:.2f} reads/sec\n")

    _print_latency_stats(_latency_stats(intervals), log_file)
    if cpu_time_total is not None:
        _print_cpu_time_stats(cpu_time_total, elapsed, log_file)
    _print_resource_stats(
        _resource_stats(
            sampler.cpu_samples if sampler else [],
            sampler.mem_samples if sampler else [],
        ),
        log_file,
    )
    return ok


# ── Driver runners ────────────────────────────────────────────────────────────
def build_and_run_c(timeout: int, log: bool, samples: int) -> bool:
    print(f"\n{'─' * 50}")
    print(" C Driver — compile + run")
    print(f"{'─' * 50}")
    log_file = None
    if log:
        log_path = (
            SCRIPT_DIR / "Logs" / "Performance" / "C"
            / f"c_driver_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        )
        log_file = open(log_path, "w")
        print(f" Logging to: {log_path.name}")

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

        system_cleanup(log_file)
        ok = capture_temperature_readings(
            "run",
            [f"./{C_BINARY}", "0x76", "0"],
            cwd=C_DIR,
            timeout=timeout,
            log_file=log_file,
            samples=samples,
        )
        print(f"\n {'PASSED' if ok else 'FAILED'}")
        return ok
    finally:
        if log_file:
            log_file.close()


def build_and_run_rust(timeout: int, log: bool, samples: int) -> bool:
    print(f"\n{'─' * 50}")
    print(" Rust Driver — cargo build + run")
    print(f"{'─' * 50}")
    log_file = None
    if log:
        log_path = (
            SCRIPT_DIR / "Logs" / "Performance" / "Rust"
            / f"rust_driver_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        )
        log_file = open(log_path, "w")
        print(f" Logging to: {log_path.name}")

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

        system_cleanup(log_file)
        ok = capture_temperature_readings(
            "rust binary",
            [str(RUST_BINARY), "0x76", "0"],
            cwd=RUST_DIR,
            timeout=timeout,
            log_file=log_file,
            samples=samples,
        )
        print(f"\n {'PASSED' if ok else 'FAILED'}")
        return ok
    finally:
        if log_file:
            log_file.close()


# ── CLI ───────────────────────────────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compile and run C/Rust drivers on Raspberry Pi 3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 run_drivers.py --c
  python3 run_drivers.py --rust
  python3 run_drivers.py --both --log
  python3 run_drivers.py --both --samples 2000
        """,
    )
    driver_group = parser.add_mutually_exclusive_group(required=False)
    driver_group.add_argument("--c", action="store_true", help="Compile and run the C driver only")
    driver_group.add_argument("--rust", action="store_true", help="Compile and run the Rust driver only")
    driver_group.add_argument("--both", action="store_true", help="Compile and run both drivers")
    parser.add_argument(
        "--samples", type=int, default=TEMP_SAMPLES, metavar="N",
        help=f"Number of temperature readings to capture (default: {TEMP_SAMPLES})",
    )
    parser.add_argument(
        "--warmup", type=int, default=WARMUP_READS, metavar="N",
        help=f"Warm-up readings discarded before measuring (default: {WARMUP_READS})",
    )
    parser.add_argument(
        "--timeout", type=int, default=60, metavar="SECONDS",
        help="Per-step timeout in seconds (default: 60)",
    )
    parser.add_argument(
        "--log", action="store_true",
        help="Save each driver's output to a timestamped .log file",
    )
    args = parser.parse_args()

    if not (args.c or args.rust or args.both):
        parser.error("one of the arguments --c --rust --both is required")
    return args


# ── Entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    args = parse_args()
    print("=" * 50)
    print(" Raspberry Pi 3 — Driver Test Runner")
    print(f" Started : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f" Root    : {ROOT_DIR}")
    print(f" Warm-up : {args.warmup} reads (excluded from metrics)")
    print("=" * 50)

    results: dict[str, bool] = {}
    if args.c or args.both:
        results["C"] = build_and_run_c(args.timeout, args.log, args.samples)
    if args.rust or args.both:
        results["Rust"] = build_and_run_rust(args.timeout, args.log, args.samples)

    print(f"\n{'=' * 50}")
    print(" Summary")
    print(f"{'=' * 50}")
    all_passed = True
    for driver, passed in results.items():
        icon = "+" if passed else "x"
        print(f" {icon} {driver} driver — {'PASSED' if passed else 'FAILED'}")
        if not passed:
            all_passed = False
    print(f"{'=' * 50}\n")
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()