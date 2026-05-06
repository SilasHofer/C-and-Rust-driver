#!/usr/bin/env python3
"""
Driver Test Runner for Raspberry Pi 3 - 48H LOW-RAM VERSION
C + Rust parallel, batch writing, minimal RAM, throttling detection, stable final summary.

Assumed folder layout (relative to this script's location):
  ../C_Driver/   <- C source files
  ../Rust_Driver/           <- Rust project (has Cargo.toml)
"""

import argparse
import math
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
import psutil
import threading
import re as _re
import collections

# ====================== PATHS ======================
SCRIPT_DIR  = Path(__file__).parent.resolve()
ROOT_DIR    = SCRIPT_DIR.parent

C_DIR       = ROOT_DIR / "C_Driver"
C_BINARY    = "c_driver"

RUST_DIR    = ROOT_DIR / "Rust_Driver"
RUST_BINARY = RUST_DIR / "target" / "release" / "bme280_bare_bones"

TEMP_PATTERN    = "Temperature:"
BATCH_SIZE      = 1500
LOG_EVERY_N     = 2000
COMPILE_TIMEOUT = 120
HEALTH_INTERVAL = 10

_BYTES_PER_LINE = 38


def get_latest_run_timestamp(log_dir: Path) -> str | None:
    """Returns the timestamp of the latest existing test run (for resume after Pi crash)."""
    if not log_dir.exists():
        return None
    files = list(log_dir.glob("c_readings_*.csv"))
    if not files:
        return None
    latest = max(files, key=lambda f: f.stat().st_mtime)
    name = latest.stem
    if name.startswith("c_readings_"):
        return name[11:]          # z.B. "20260422_141239"
    return None


# ====================== LOW-RAM HELPERS ======================
class BatchWriter:
    def __init__(self, csv_path: Path):
        self.csv_path = csv_path
        self.batch: list[str] = []
        self.total_reads = 0
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        if not csv_path.exists():
            with open(csv_path, "w") as f:
                f.write("timestamp,temperature_C\n")

    def add(self, temp: float) -> None:
        ts_str = datetime.now().isoformat(timespec="microseconds")
        self.batch.append(f"{ts_str},{temp:.3f}\n")
        self.total_reads += 1
        if len(self.batch) >= BATCH_SIZE:
            self.flush()

    def flush(self) -> None:
        if self.batch:
            with open(self.csv_path, "a") as f:
                f.writelines(self.batch)
            self.batch.clear()

    def final_flush(self) -> None:
        self.flush()


class RunningStats:
    def __init__(self):
        self.n = 0
        self.mean = 0.0
        self.M2 = 0.0
        self.min_v = float("inf")
        self.max_v = float("-inf")
        self.spike_count = 0
        self._window = collections.deque(maxlen=3)

    def update(self, x: float) -> None:
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        self.M2 += delta * (x - self.mean)
        self.min_v = min(self.min_v, x)
        self.max_v = max(self.max_v, x)
        self._window.append(x)
        if len(self._window) == 3 and self.n > 10:
            std = self.std()
            if std > 0:
                prev, cur, nxt = self._window
                if abs(cur - prev) > 3 * std and abs(cur - nxt) > 3 * std:
                    self.spike_count += 1

    def std(self) -> float:
        return math.sqrt(self.M2 / (self.n - 1)) if self.n >= 2 else 0.0

    def snapshot(self) -> str:
        min_str = f"{self.min_v:.3f}" if self.min_v != float("inf") else "n/a"
        max_str = f"{self.max_v:.3f}" if self.max_v != float("-inf") else "n/a"
        return (f"n={self.n:,} mean={self.mean:.3f}C std={self.std():.3f}C "
                f"min={min_str}C max={max_str}C spikes={self.spike_count}")


class ResourceSampler:
    def __init__(self, pid: int, interval: float = 0.5):
        self._pid_lock = threading.Lock()
        self._proc = psutil.Process(pid)
        self.interval = interval
        self._n = 0
        self._mean = 0.0
        self._M2 = 0.0
        self._min = float("inf")
        self._max = float("-inf")
        self._running = False
        self._thread = None

    def update_pid(self, pid: int) -> None:
        with self._pid_lock:
            self._proc = psutil.Process(pid)

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while self._running:
            try:
                with self._pid_lock:
                    rss = self._proc.memory_info().rss
                self._n += 1
                delta = rss - self._mean
                self._mean += delta / self._n
                self._M2 += delta * (rss - self._mean)
                self._min = min(self._min, rss)
                self._max = max(self._max, rss)
            except psutil.NoSuchProcess:
                time.sleep(self.interval)
            except Exception:
                pass
            time.sleep(self.interval)

    @property
    def mean_mb(self) -> float:
        return self._mean / 1024 / 1024

    @property
    def min_mb(self) -> float:
        return self._min / 1024 / 1024 if self._min != float("inf") else 0.0

    @property
    def max_mb(self) -> float:
        return self._max / 1024 / 1024 if self._max != float("-inf") else 0.0

    @property
    def std_mb(self) -> float:
        return (math.sqrt(self._M2 / (self._n - 1)) / 1024 / 1024) if self._n >= 2 else 0.0

    def snapshot(self) -> str:
        return (f"RAM mean={self.mean_mb:.2f}MB min={self.min_mb:.2f}MB "
                f"max={self.max_mb:.2f}MB std={self.std_mb:.2f}MB")


class DriverLogger:
    def __init__(self, path: Path, enabled: bool):
        self.path = path
        self.enabled = enabled
        self._lock = threading.Lock()
        self._fh = open(path, "a", encoding="utf-8") if enabled else None

    def write(self, line: str) -> None:
        if not self._fh:
            return
        try:
            with self._lock:
                self._fh.write(line if line.endswith("\n") else line + "\n")
                self._fh.flush()
        except OSError as e:
            print(f"  [LOG WRITE ERROR] {self.path.name}: {e}")

    def close(self) -> None:
        if self._fh:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None


# ====================== SYSTEM HELPERS ======================
def get_cpu_temp() -> float:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return float(f.read().strip()) / 1000.0
    except Exception:
        return -999.0


def get_throttled_status() -> str:
    try:
        result = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=2)
        out = result.stdout.strip()
        return out.split("throttled=")[1].strip() if "throttled=" in out else out
    except Exception:
        return "ERROR"


def run_step(label: str, cmd: list, cwd: Path, timeout: int) -> bool:
    print(f"\n  $ {' '.join(str(c) for c in cmd)}")
    try:
        result = subprocess.run([str(c) for c in cmd], cwd=str(cwd), timeout=timeout,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        print(result.stdout, end="")
        return result.returncode == 0
    except Exception as e:
        print(f"  x {label} failed: {e}")
        return False


def check_disk_space(log_dir: Path, hz: float, duration_s: int) -> None:
    est_hz = 150.0 if hz == 0 else hz
    est_bytes = est_hz * duration_s * _BYTES_PER_LINE * 2
    est_gb = est_bytes / 1024 ** 3
    label = "full speed" if hz == 0 else f"{hz} Hz"
    try:
        free_gb = shutil.disk_usage(log_dir).free / 1024 ** 3
    except Exception:
        free_gb = -1.0
    print(f"\n  Disk space check ({label}):")
    print(f"    Estimated CSV output : ~{est_gb:.2f} GB")
    if free_gb >= 0:
        print(f"    Available on SD card : {free_gb:.2f} GB")
        if free_gb < est_gb * 1.2:
            print("  WARNING: Less than 20% headroom!")
    if hz == 0:
        print("  INFO: Consider --hz 10; full speed adds unnecessary SD card wear.")


def make_reader_thread(label, proc, batch_writer, stats, driver_log):
    def _run():
        while True:
            try:
                line = proc.stdout.readline()
            except Exception:
                break
            if not line:
                break
            line = line.rstrip()
            if TEMP_PATTERN in line:
                m = _re.search(r"Temperature:\s*([-+]?[0-9]*\.?[0-9]+)", line)
                if m:
                    try:
                        temp = float(m.group(1))
                        batch_writer.add(temp)
                        stats.update(temp)
                        if batch_writer.total_reads % LOG_EVERY_N == 0:
                            msg = f"Progress: {batch_writer.total_reads:,} readings"
                            print(f"  {label} {msg}")
                            driver_log.write(msg)
                    except Exception:
                        pass
            else:
                print(f"  {label}: {line}")
                driver_log.write(line)
    t = threading.Thread(target=_run, daemon=True, name=f"reader-{label}")
    return t


# ====================== MAIN ======================
def main() -> None:
    parser = argparse.ArgumentParser(description="48h Low-RAM Driver Stress-Test")
    parser.add_argument("--parallel", action="store_true", required=True)
    parser.add_argument("--duration", type=int, default=172800, help="Runtime in seconds (default 48 h)")
    parser.add_argument("--hz", type=float, default=0, help="Sample rate Hz; 0 = full speed")
    parser.add_argument("--no-log", action="store_true", default=False)
    args = parser.parse_args()
    log_enabled = not args.no_log

    print("=" * 70)
    print("  Raspberry Pi 3 -- 48H LOW-RAM PARALLEL TEST")
    print(f"  Started : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Duration: {args.duration} s  ({args.duration / 3600:.1f} h)")
    print("=" * 70)

    # Compile
    run_step("gcc compile", ["gcc", "-std=c11", "-D_DEFAULT_SOURCE", "-Wall", "-Wextra", "-O2",
                             "main.c", "bme280.c", "i2c_linux.c", "-o", C_BINARY], C_DIR, COMPILE_TIMEOUT)
    run_step("cargo build", ["cargo", "build", "--release"], RUST_DIR, COMPILE_TIMEOUT)

    # Log directory
    log_dir = SCRIPT_DIR / "Logs" / "Reliability" / "parallel"
    log_dir.mkdir(parents=True, exist_ok=True)

    # === RESUME LOGIC AFTER PI CRASH ===
    existing_ts = get_latest_run_timestamp(log_dir)
    if existing_ts:
        ts = existing_ts
        print(f"✅ RESUMING previous test (timestamp: {ts})")
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        print(f"Starting NEW test run (timestamp: {ts})")

    c_csv = log_dir / f"c_readings_{ts}.csv"
    rust_csv = log_dir / f"rust_readings_{ts}.csv"

    c_log = DriverLogger(log_dir / f"c_driver_{ts}.log", log_enabled)
    r_log = DriverLogger(log_dir / f"rust_driver_{ts}.log", log_enabled)
    sys_log = DriverLogger(log_dir / f"system_{ts}.log", log_enabled)

    readings_c = BatchWriter(c_csv)
    readings_rust = BatchWriter(rust_csv)

    # Header
    if existing_ts is None:
        header = (f"Test started : {datetime.now().isoformat()}\n"
                  f"Duration     : {args.duration} s\n"
                  f"Hz           : {args.hz if args.hz else 'full speed'}\n")
        for lg in (c_log, r_log, sys_log):
            lg.write(header)
    else:
        resume_msg = f"RESUMED at {datetime.now().isoformat()}\n"
        for lg in (c_log, r_log, sys_log):
            lg.write(resume_msg)

    # Launch drivers
    c_cmd = [f"./{C_BINARY}", "0x76", str(args.hz), "/dev/i2c-1", "--coord-lock"]
    rust_cmd = [str(RUST_BINARY), "0x76", str(args.hz), "/dev/i2c-1", "--coord-lock"]

    c_proc = subprocess.Popen(c_cmd, cwd=str(C_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    time.sleep(0.2)
    rust_proc = subprocess.Popen(rust_cmd, cwd=str(RUST_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

    c_sampler = ResourceSampler(c_proc.pid)
    rust_sampler = ResourceSampler(rust_proc.pid)
    c_sampler.start()
    rust_sampler.start()

    c_stats = RunningStats()
    rust_stats = RunningStats()

    max_cpu_temp = 0.0
    throttle_events = 0
    c_restarts = 0
    rust_restarts = 0

    ct = make_reader_thread("C_driver", c_proc, readings_c, c_stats, c_log)
    rt = make_reader_thread("Rust_driver", rust_proc, readings_rust, rust_stats, r_log)
    ct.start()
    rt.start()

    print("\n Both drivers running -- 48h Low-RAM test started!\n")

    last_check = time.monotonic()
    start_time = time.monotonic()

    try:
        while time.monotonic() - start_time < args.duration:
            time.sleep(0.1)

            if time.monotonic() - last_check >= HEALTH_INTERVAL:
                now = datetime.now().isoformat(timespec="seconds")
                cpu_temp = get_cpu_temp()
                throttled = get_throttled_status()

                if cpu_temp > max_cpu_temp and cpu_temp > 0:
                    max_cpu_temp = cpu_temp

                c_log.write(f"[{now}] CPU={cpu_temp:.1f}C throttled={throttled}\n"
                            f"[{now}] Temp  {c_stats.snapshot()}\n"
                            f"[{now}] {c_sampler.snapshot()}\n"
                            f"[{now}] reads={readings_c.total_reads:,} restarts={c_restarts}")
                r_log.write(f"[{now}] CPU={cpu_temp:.1f}C throttled={throttled}\n"
                            f"[{now}] Temp  {rust_stats.snapshot()}\n"
                            f"[{now}] {rust_sampler.snapshot()}\n"
                            f"[{now}] reads={readings_rust.total_reads:,} restarts={rust_restarts}")
                sys_log.write(f"[{now}] CPU={cpu_temp:.1f}C throttled={throttled} "
                              f"c_reads={readings_c.total_reads:,} rust_reads={readings_rust.total_reads:,} "
                              f"max_cpu={max_cpu_temp:.1f}C throttle_events={throttle_events}")

                if throttled not in ("0x0", "ERROR"):
                    throttle_events += 1
                    warning = f"WARNING: THROTTLING DETECTED! vcgencmd = {throttled}"
                    print(f"  {warning}")
                    sys_log.write(warning)

                last_check = time.monotonic()

            # Crash & Restart
            if c_proc.poll() is not None:
                c_restarts += 1
                msg = f"WARNING: C_driver crashed! Restart #{c_restarts}"
                print(f"  {msg}")
                c_log.write(msg)
                sys_log.write(msg)
                c_proc = subprocess.Popen(c_cmd, cwd=str(C_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                c_sampler.update_pid(c_proc.pid)
                ct = make_reader_thread("C_driver", c_proc, readings_c, c_stats, c_log)
                ct.start()

            if rust_proc.poll() is not None:
                rust_restarts += 1
                msg = f"WARNING: Rust_driver crashed! Restart #{rust_restarts}"
                print(f"  {msg}")
                r_log.write(msg)
                sys_log.write(msg)
                rust_proc = subprocess.Popen(rust_cmd, cwd=str(RUST_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                rust_sampler.update_pid(rust_proc.pid)
                rt = make_reader_thread("Rust_driver", rust_proc, readings_rust, rust_stats, r_log)
                rt.start()

    except KeyboardInterrupt:
        print("\n Test manually stopped.")

    finally:
        readings_c.final_flush()
        readings_rust.final_flush()
        c_sampler.stop()
        rust_sampler.stop()

        for p in (c_proc, rust_proc):
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    p.kill()

        elapsed = max(time.monotonic() - start_time, 0.001)

        def _safe_temp_str(v: float, fallback: str = "n/a") -> str:
            if v in (float("inf"), float("-inf")):
                return fallback
            return f"{v:8.3f}"

        summary_lines = [
            "", "="*70,
            "  FINAL SUMMARY -- 48H LOW-RAM TEST",
            "="*70,
            f"  Elapsed          : {elapsed:.1f} s  ({elapsed / 3600:.2f} h)",
            "",
            "-- C Driver ----------------------------------------------------------",
            f"  Total readings   : {readings_c.total_reads:,}",
            f"  Throughput       : {readings_c.total_reads / elapsed:.2f} reads/sec",
            f"  Restarts         : {c_restarts}",
            f"  Temp mean        : {c_stats.mean:8.3f} C",
            f"  Temp std-dev     : {c_stats.std():8.3f} C",
            f"  Temp min         : {_safe_temp_str(c_stats.min_v)} C",
            f"  Temp max         : {_safe_temp_str(c_stats.max_v)} C",
            f"  Spike count      : {c_stats.spike_count}",
            f"  RAM mean         : {c_sampler.mean_mb:6.2f} MB",
            f"  RAM min          : {c_sampler.min_mb:6.2f} MB",
            f"  RAM max          : {c_sampler.max_mb:6.2f} MB",
            f"  RAM std-dev      : {c_sampler.std_mb:6.2f} MB",
            "",
            "-- Rust Driver -------------------------------------------------------",
            f"  Total readings   : {readings_rust.total_reads:,}",
            f"  Throughput       : {readings_rust.total_reads / elapsed:.2f} reads/sec",
            f"  Restarts         : {rust_restarts}",
            f"  Temp mean        : {rust_stats.mean:8.3f} C",
            f"  Temp std-dev     : {rust_stats.std():8.3f} C",
            f"  Temp min         : {_safe_temp_str(rust_stats.min_v)} C",
            f"  Temp max         : {_safe_temp_str(rust_stats.max_v)} C",
            f"  Spike count      : {rust_stats.spike_count}",
            f"  RAM mean         : {rust_sampler.mean_mb:6.2f} MB",
            f"  RAM min          : {rust_sampler.min_mb:6.2f} MB",
            f"  RAM max          : {rust_sampler.max_mb:6.2f} MB",
            f"  RAM std-dev      : {rust_sampler.std_mb:6.2f} MB",
            "",
            "-- System ------------------------------------------------------------",
            f"  Max CPU temp     : {max_cpu_temp:.1f} C",
            f"  Throttle events  : {throttle_events}",
            "",
            f"-- Output files in: {log_dir}",
            f"  {c_csv.name}",
            f"  {rust_csv.name}",
            f"  {(log_dir / f'c_driver_{ts}.log').name}",
            f"  {(log_dir / f'rust_driver_{ts}.log').name}",
            f"  {(log_dir / f'system_{ts}.log').name}",
            "="*70,
        ]

        output = "\n".join(summary_lines)
        print(output)
        sys_log.write(output)

        c_log.close()
        r_log.close()
        sys_log.close()


if __name__ == "__main__":
    main()