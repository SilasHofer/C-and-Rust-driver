#!/usr/bin/env python3
"""
Driver Test Runner for Raspberry Pi 3
Runs C and Rust drivers on separate software I2C buses.
"""

import argparse
import math
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
import threading
import re as _re
from typing import Dict, List, Tuple

# ------------------- Paths -------------------
SCRIPT_DIR  = Path(__file__).parent.resolve()
ROOT_DIR    = SCRIPT_DIR.parent

C_DIR       = ROOT_DIR / "C_Driver"
C_BINARY    = "c_driver"

RUST_DIR    = ROOT_DIR / "Rust_Driver"
RUST_BINARY = RUST_DIR / "target" / "release" / "bme280_bare_bones"
# ---------------------------------------------

TEMP_PATTERN = "Temperature:"
LOG_EVERY_N  = 100
COMPILE_TIMEOUT = 120

# ------------------- Utils -------------------
def run_step(label: str, cmd: list, cwd: Path, timeout: int, log_file) -> bool:
    print(f"\n  $ {' '.join(str(c) for c in cmd)}")
    try:
        result = subprocess.run([str(c) for c in cmd], cwd=str(cwd),
                                timeout=timeout, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
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

# ------------------- Build drivers -------------------
def build_c_driver(log_file):
    print("\nBuilding C driver...")
    return run_step("gcc compile",
                    ["gcc", "-std=c11", "-D_DEFAULT_SOURCE", "-Wall", "-Wextra", "-O2",
                     "main.c", "bme280.c", "i2c_linux.c", "-o", C_BINARY],
                    C_DIR, COMPILE_TIMEOUT, log_file)

def build_rust_driver(log_file):
    print("\nBuilding Rust driver...")
    return run_step("cargo build", ["cargo","build","--release"], RUST_DIR, COMPILE_TIMEOUT, log_file)


def _series_stats(values: List[float]) -> Dict[str, float]:
    if not values:
        return {}
    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    return {
        "count": n,
        "mean": mean,
        "std": math.sqrt(variance),
        "min": min(values),
        "max": max(values),
    }


def _pair_by_time(
    c_samples: List[Tuple[float, float]],
    rust_samples: List[Tuple[float, float]],
    max_gap_s: float,
) -> List[Tuple[float, float, float, float, float]]:
    """
    Pair each C sample with at most one Rust sample based on nearest timestamp.
    Returns tuples: (c_ts, c_temp, rust_ts, rust_temp, abs_delta_temp).
    """
    pairs: List[Tuple[float, float, float, float, float]] = []
    i = 0
    j = 0
    while i < len(c_samples) and j < len(rust_samples):
        c_ts, c_temp = c_samples[i]
        r_ts, r_temp = rust_samples[j]
        dt = r_ts - c_ts

        if abs(dt) <= max_gap_s:
            pairs.append((c_ts, c_temp, r_ts, r_temp, abs(c_temp - r_temp)))
            i += 1
            j += 1
            continue

        if dt < 0:
            j += 1
        else:
            i += 1

    return pairs

# ------------------- Main -------------------
def parse_args():
    parser = argparse.ArgumentParser(description="Run C/Rust drivers on Pi3")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--log", action="store_true", default=True)
    parser.add_argument("--parallel", action="store_true")
    parser.add_argument("--c_bus", type=str, default="/dev/i2c-1", help="I2C bus for C driver")
    parser.add_argument("--rust_bus", type=str, default="/dev/i2c-1", help="I2C bus for Rust driver")
    parser.add_argument("--c_addr", type=str, default="76", help="I2C address for C driver (hex)")
    parser.add_argument("--rust_addr", type=str, default="76", help="I2C address for Rust driver (hex)")
    parser.add_argument("--c_freq", type=int, default=0, help="I2C frequency for C driver (0=max)")
    parser.add_argument("--rust_freq", type=int, default=0, help="I2C frequency for Rust driver (0=max)")
    parser.add_argument("--stagger", type=float, default=0, help="Delay in seconds before starting Rust driver (reduces contention)")
    parser.add_argument("--pair_gap", type=float, default=0.2, help="Max timestamp gap in seconds for C/Rust sample pairing")
    return parser.parse_args()

def main():
    args = parse_args()
    log_file = None
    if args.log:
        log_file = open(SCRIPT_DIR / f"drivers_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log","w")

    # Build drivers once
    if not build_c_driver(log_file):
        if log_file:
            log_file.close()
        sys.exit(1)
    if not build_rust_driver(log_file):
        if log_file:
            log_file.close()
        sys.exit(1)

    if args.parallel:
        print("\nStarting parallel temperature capture...")
        print(f"C driver on bus: {args.c_bus} (address: {args.c_addr})")
        print(f"Rust driver on bus: {args.rust_bus} (address: {args.rust_addr})")
        print(f"Timeout: {args.timeout} seconds\n")
        
        if log_file:
            log_file.write(f"\n=== Parallel Execution Start ===\n")
            log_file.write(f"C driver bus: {args.c_bus} (address: {args.c_addr})\n")
            log_file.write(f"Rust driver bus: {args.rust_bus} (address: {args.rust_addr})\n")
            log_file.write(f"Timeout: {args.timeout}s\n\n")
        
        start_time = time.monotonic()
        c_cmd = [str(C_DIR / C_BINARY), args.c_addr, str(args.c_freq), args.c_bus, "--coord-lock"]
        rust_cmd = [str(RUST_BINARY), args.rust_addr, str(args.rust_freq), args.rust_bus, "--coord-lock"]
        
        # Start both driver processes
        print("Starting C driver...")
        c_proc = subprocess.Popen([str(c) for c in c_cmd], cwd=str(C_DIR),
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        
        # Stagger Rust driver start if on same bus to reduce contention
        if args.stagger > 0:
            time.sleep(args.stagger)
        
        print("Starting Rust driver...")
        rust_proc = subprocess.Popen([str(c) for c in rust_cmd], cwd=str(RUST_DIR),
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        
        # Shared sample storage
        samples: Dict[str, List[Tuple[float, float]]] = {"C_driver": [], "Rust_driver": []}
        samples_lock = threading.Lock()

        def reader_thread(label: str, proc: subprocess.Popen):
            while not stop_event.is_set():
                line = proc.stdout.readline()
                if not line:
                    break
                line = line.rstrip()
                
                if TEMP_PATTERN in line:
                    timestamp = datetime.now().isoformat()
                    temp_match = _re.search(r"Temperature:\s*([-+]?[0-9]*\.?[0-9]+)", line)
                    if temp_match:
                        sample = (time.monotonic(), float(temp_match.group(1)))
                        with samples_lock:
                            samples[label].append(sample)
                        output_line = f"{label} [{timestamp}]: {line}"
                    else:
                        output_line = f"{label}: {line}"
                else:
                    output_line = f"{label}: {line}"
                
                print(output_line)
                if log_file:
                    log_file.write(output_line + "\n")
                    log_file.flush()

        # Create stop event for threads
        stop_event = threading.Event()
        
        # Start reader threads for both drivers
        c_thread = threading.Thread(target=reader_thread, args=("C_driver", c_proc), daemon=True)
        rust_thread = threading.Thread(target=reader_thread, args=("Rust_driver", rust_proc), daemon=True)
        
        c_thread.start()
        rust_thread.start()
        
        # Monitor timeout
        try:
            while time.monotonic() - start_time < args.timeout:
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\nInterrupted by user.")
        
        elapsed = time.monotonic() - start_time
        print(f"\nTest duration: {elapsed:.2f}s")
        
        # Signal threads to stop
        stop_event.set()
        
        # Terminate both processes
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
        
        # Wait for threads to finish
        c_thread.join(timeout=1)
        rust_thread.join(timeout=1)
        
        with samples_lock:
            c_samples = list(samples["C_driver"])
            rust_samples = list(samples["Rust_driver"])

        c_values = [temp for _, temp in c_samples]
        rust_values = [temp for _, temp in rust_samples]
        c_stats = _series_stats(c_values)
        rust_stats = _series_stats(rust_values)
        pairs = _pair_by_time(c_samples, rust_samples, args.pair_gap)
        pair_deltas = [p[4] for p in pairs]
        delta_stats = _series_stats(pair_deltas)

        print("\nParallel summary:")
        print(f"C readings: {len(c_values)}")
        print(f"Rust readings: {len(rust_values)}")
        print(f"Paired readings (gap <= {args.pair_gap:.3f}s): {len(pairs)}")

        if c_stats:
            print(f"C mean/std/range: {c_stats['mean']:.3f}/{c_stats['std']:.3f}/{(c_stats['max']-c_stats['min']):.3f} C")
        if rust_stats:
            print(f"Rust mean/std/range: {rust_stats['mean']:.3f}/{rust_stats['std']:.3f}/{(rust_stats['max']-rust_stats['min']):.3f} C")
        if delta_stats:
            print(f"|C-Rust| mean/std/max: {delta_stats['mean']:.4f}/{delta_stats['std']:.4f}/{delta_stats['max']:.4f} C")
        else:
            print("No paired temperature samples available for delta analysis.")

        if log_file:
            log_file.write(f"\n=== Parallel Execution End (elapsed: {elapsed:.2f}s) ===\n")
            log_file.write(f"C readings: {len(c_values)}\n")
            log_file.write(f"Rust readings: {len(rust_values)}\n")
            log_file.write(f"Paired readings (gap <= {args.pair_gap:.3f}s): {len(pairs)}\n")
            if c_stats:
                log_file.write(
                    "C mean/std/range: "
                    f"{c_stats['mean']:.3f}/{c_stats['std']:.3f}/{(c_stats['max']-c_stats['min']):.3f} C\n"
                )
            if rust_stats:
                log_file.write(
                    "Rust mean/std/range: "
                    f"{rust_stats['mean']:.3f}/{rust_stats['std']:.3f}/{(rust_stats['max']-rust_stats['min']):.3f} C\n"
                )
            if delta_stats:
                log_file.write(
                    "|C-Rust| mean/std/max: "
                    f"{delta_stats['mean']:.4f}/{delta_stats['std']:.4f}/{delta_stats['max']:.4f} C\n"
                )

    if log_file:
        log_file.close()
        print(f"\nLog saved to: {log_file.name}")
    print("Done.")

if __name__=="__main__":
    main()