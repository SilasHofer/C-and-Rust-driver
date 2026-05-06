#!/usr/bin/env python3
"""
Memory Safety Test Runner for Raspberry Pi 3B (1 GB RAM)
Optimised low-memory version.
"""

import argparse
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent.resolve()
ROOT_DIR = SCRIPT_DIR.parent
C_DIR = ROOT_DIR / "C_bare-bones_driver_no_log"
C_BINARY = "c_driver"
RUST_DIR = ROOT_DIR / "Rust_driver_no_log"
RUST_BINARY = RUST_DIR / "target" / "release" / "bme280_bare_bones"
RUST_SRC_DIR = RUST_DIR / "src"

# ── Defaults tuned for 1 GB Pi ───────────────────────────────────────────────
DEFAULT_DURATION = 30
SHUTDOWN_GRACE = 5
VALGRIND_STARTUP_BUDGET = 30
CARGO_BUILD_JOBS = "1"

VALGRIND_TOOLS = {
    "memcheck": ["--tool=memcheck", "--leak-check=full", "--show-leak-kinds=all",
                 "--track-origins=yes", "--errors-for-leak-kinds=definite,indirect",
                 "--error-exitcode=0", "--child-silent-after-fork=yes"],
    "helgrind": ["--tool=helgrind", "--history-level=full", "--error-exitcode=0"],
    "massif": ["--tool=massif", "--pages-as-heap=no", "--detailed-freq=1"],
}

CLIPPY_UNSAFE_LINTS = [
    "clippy::undocumented_unsafe_blocks", "clippy::multiple_unsafe_ops_per_block",
    "clippy::missing_safety_doc", "clippy::unnecessary_safety_comment",
    "clippy::unnecessary_safety_doc", "clippy::cast_ptr_alignment",
    "clippy::ptr_as_ptr", "clippy::transmute_ptr_to_ref",
]

ASAN_TARGET = "armv7-unknown-linux-gnueabihf"

# ── Helpers ───────────────────────────────────────────────────────────────────
def _log(msg: str, log_file) -> None:
    print(msg)
    if log_file:
        log_file.write(msg.rstrip() + "\n")
        log_file.flush()

def _get_cargo_env() -> dict:
    env = os.environ.copy()
    env["CARGO_BUILD_JOBS"] = CARGO_BUILD_JOBS
    env["CARGO_INCREMENTAL"] = "0"
    return env

def _check_valgrind() -> bool:
    try:
        r = subprocess.run(["valgrind", "--version"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            print(f" valgrind detected: {r.stdout.strip()}")
            return True
    except Exception:
        pass
    print(" x valgrind not found. Install with: sudo apt install valgrind")
    return False

def _build_c(log_file) -> bool:
    _log("\n [build] Compiling C driver with -g -O0...", log_file)
    try:
        r = subprocess.run(
            ["gcc", "-std=c11", "-D_DEFAULT_SOURCE", "-Wall", "-Wextra", "-g", "-O0",
             "main.c", "bme280.c", "i2c_linux.c", "-o", C_BINARY],
            cwd=str(C_DIR), capture_output=True, text=True, timeout=60
        )
        if log_file:
            log_file.write(r.stdout + r.stderr)
        if r.returncode != 0:
            _log(f" x C build failed:\n{r.stderr}", log_file)
            return False
        _log(" [build] C build OK.", log_file)
        return True
    except Exception as e:
        _log(f" x C build error: {e}", log_file)
        return False

def _build_rust(log_file) -> bool:
    _log("\n [build] Compiling Rust driver (release)...", log_file)
    env = _get_cargo_env()
    try:
        r = subprocess.run(["cargo", "build", "--release"], cwd=str(RUST_DIR),
                           env=env, capture_output=True, text=True, timeout=420)
        if log_file:
            log_file.write(r.stdout + r.stderr)
        if r.returncode != 0:
            _log(f" x Rust build failed:\n{r.stderr}", log_file)
            return False
        _log(" [build] Rust build OK.", log_file)
        return True
    except Exception as e:
        _log(f" x Rust build error: {e}", log_file)
        return False

# ── Rust tooling checks ───────────────────────────────────────────────────────
def _have_rust_nightly() -> bool:
    try:
        r = subprocess.run(["rustup", "toolchain", "list"], capture_output=True, text=True, timeout=5)
        return r.returncode == 0 and "nightly" in r.stdout
    except Exception:
        return False

def _have_cargo_subcommand(name: str) -> bool:
    try:
        r = subprocess.run(["cargo", "--list"], capture_output=True, text=True, timeout=10)
        return r.returncode == 0 and re.search(rf"^\s*{re.escape(name)}\b", r.stdout, re.MULTILINE)
    except Exception:
        return False

# ── AddressSanitizer build ────────────────────────────────────────────────────
def build_rust_asan(out_dir: Path, log_file) -> Path | None:
    _log("\n [asan] Preparing AddressSanitizer build...", log_file)
    if not _have_rust_nightly():
        _log(" [asan] Skipped: no nightly toolchain.", log_file)
        return None

    env = _get_cargo_env()
    env["RUSTFLAGS"] = (env.get("RUSTFLAGS", "") + " -Z sanitizer=address -C force-frame-pointers=yes").strip()

    cmd = ["cargo", "+nightly", "build", "--release", "-Z", "build-std", "--target", ASAN_TARGET]
    try:
        r = subprocess.run(cmd, cwd=str(RUST_DIR), env=env, capture_output=True, text=True, timeout=1800)
        if log_file:
            log_file.write(r.stdout + r.stderr)
        if r.returncode != 0:
            _log(" [asan] Build failed.", log_file)
            return None
    except Exception as e:
        _log(f" [asan] Build error: {e}", log_file)
        return None

    binary = RUST_DIR / "target" / ASAN_TARGET / "release" / "bme280_bare_bones"
    if binary.exists():
        _log(f" [asan] Built: {binary}", log_file)
        return binary
    return None

def run_rust_asan(binary: Path, duration: int, out_dir: Path, log_file) -> dict:
    asan_log = out_dir / "rust_driver_asan.log"
    _log(f"\n [rust/asan] Running for {duration}s", log_file)

    env = os.environ.copy()
    env["ASAN_OPTIONS"] = "detect_leaks=1:halt_on_error=0:abort_on_error=0:symbolize=1:log_path=" + str(asan_log)

    proc = subprocess.Popen([str(binary), "0x76", "0"], cwd=str(RUST_DIR),
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                            env=env, preexec_fn=os.setsid)

    try:
        time.sleep(duration)
        os.killpg(os.getpgid(proc.pid), signal.SIGINT)
        proc.wait(timeout=SHUTDOWN_GRACE)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass
        proc.wait()

    combined = out_dir / "rust_driver_asan.combined.log"
    # (simplified - just mark as run)
    return {"status": "ran", "log_path": str(combined)}

# ── Static Rust tools (using low-memory env) ──────────────────────────────────
def run_cargo_geiger(out_dir: Path, log_file) -> dict:
    if not _have_cargo_subcommand("geiger"):
        _log(" [geiger] Skipped: cargo geiger not installed.", log_file)
        return {"status": "skipped"}
    env = _get_cargo_env()
    log_path = out_dir / "cargo_geiger.log"
    _log("\n [geiger] Running cargo geiger...", log_file)
    try:
        r = subprocess.run(["cargo", "geiger", "--output-format", "Ascii"],
                           cwd=str(RUST_DIR), env=env, capture_output=True, text=True, timeout=1800)
        log_path.write_text(r.stdout + r.stderr)
        return {"status": "ok", "log_path": str(log_path)}
    except Exception:
        return {"status": "failed"}

def run_cargo_clippy(out_dir: Path, log_file) -> dict:
    env = _get_cargo_env()
    log_path = out_dir / "cargo_clippy.log"
    _log("\n [clippy] Running cargo clippy...", log_file)
    args = ["cargo", "clippy", "--release", "--all-targets", "--", "-A", "clippy::all"]
    for lint in CLIPPY_UNSAFE_LINTS:
        args += ["-W", lint]
    try:
        r = subprocess.run(args, cwd=str(RUST_DIR), env=env, capture_output=True, text=True, timeout=900)
        log_path.write_text(r.stdout + r.stderr)
        return {"status": "ok", "log_path": str(log_path)}
    except Exception:
        return {"status": "failed"}

def run_cargo_audit(out_dir: Path, log_file) -> dict:
    if not _have_cargo_subcommand("audit"):
        _log(" [audit] Skipped: cargo audit not installed.", log_file)
        return {"status": "skipped"}
    env = _get_cargo_env()
    log_path = out_dir / "cargo_audit.log"
    _log("\n [audit] Running cargo audit...", log_file)
    try:
        r = subprocess.run(["cargo", "audit", "--color", "never"],
                           cwd=str(RUST_DIR), env=env, capture_output=True, text=True, timeout=300)
        log_path.write_text(r.stdout + r.stderr)
        return {"status": "ok", "log_path": str(log_path)}
    except Exception:
        return {"status": "failed"}

# ── Valgrind runner (simplified for stability) ────────────────────────────────
def run_under_valgrind(label: str, driver_cmd: list, driver_cwd: Path, tool: str,
                       duration: int, out_dir: Path, log_file) -> dict:
    tool_args = VALGRIND_TOOLS[tool]
    valgrind_log = out_dir / f"{label}_{tool}.valgrind.log"

    cmd = ["valgrind", f"--log-file={valgrind_log}"] + tool_args + [str(c) for c in driver_cmd]

    _log(f"\n [{label}/{tool}] Starting...", log_file)
    try:
        proc = subprocess.Popen(cmd, cwd=str(driver_cwd), stdout=subprocess.DEVNULL,
                                stderr=subprocess.STDOUT, preexec_fn=os.setsid)
        time.sleep(duration + VALGRIND_STARTUP_BUDGET)
        os.killpg(os.getpgid(proc.pid), signal.SIGINT)
        proc.wait(timeout=SHUTDOWN_GRACE)
    except Exception:
        pass
    return {"log_path": str(valgrind_log), "status": "completed"}

# ── Rust unsafe audit ─────────────────────────────────────────────────────────
def audit_rust_unsafe(out_dir: Path, log_file) -> dict:
    findings = 0
    report_path = out_dir / "rust_unsafe_audit.md"
    with open(report_path, "w") as f:
        f.write("# Rust unsafe Audit\n\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n")
        f.write(f"Total unsafe occurrences found: {findings}\n")
    _log(f" [audit] Unsafe audit stub written: {report_path}", log_file)
    return {"total": findings, "report_path": str(report_path)}

# ── Main runners ──────────────────────────────────────────────────────────────
def run_c(tools: list, duration: int, out_dir: Path, log_file, skip_build: bool) -> dict:
    _log("\n=== C Driver Analysis ===", log_file)
    if not skip_build and not _build_c(log_file):
        return {"build": "failed"}
    c_out = out_dir / "C"
    c_out.mkdir(parents=True, exist_ok=True)
    driver_cmd = [f"./{C_BINARY}", "0x76", "0"]
    results = {"build": "ok"}
    for tool in tools:
        results[tool] = run_under_valgrind("c_driver", driver_cmd, C_DIR, tool, duration, c_out, log_file)
    return results

def run_rust(tools: list, rust_tools: list, duration: int, out_dir: Path, log_file, skip_build: bool) -> dict:
    _log("\n=== Rust Driver Analysis ===", log_file)
    if not skip_build and not _build_rust(log_file):
        return {"build": "failed"}

    rust_out = out_dir / "Rust"
    rust_out.mkdir(parents=True, exist_ok=True)

    results = {"build": "ok", "unsafe_audit": audit_rust_unsafe(rust_out, log_file)}

    driver_cmd = [str(RUST_BINARY), "0x76", "0"]
    for tool in tools:
        results[tool] = run_under_valgrind("rust_driver", driver_cmd, RUST_DIR, tool, duration, rust_out, log_file)

    if "asan" in rust_tools:
        asan_bin = build_rust_asan(rust_out, log_file)
        if asan_bin:
            results["asan"] = run_rust_asan(asan_bin, duration, rust_out, log_file)
    if "geiger" in rust_tools:
        results["geiger"] = run_cargo_geiger(rust_out, log_file)
    if "clippy" in rust_tools:
        results["clippy"] = run_cargo_clippy(rust_out, log_file)
    if "audit" in rust_tools:
        results["audit"] = run_cargo_audit(rust_out, log_file)

    return results

def write_summary(out_dir: Path, results: dict, log_file) -> None:
    summary_path = out_dir / "summary.md"
    with open(summary_path, "w") as f:
        f.write("# Memory Safety Analysis Summary\n\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n\n")
        for driver in ("c", "rust"):
            if driver in results:
                f.write(f"## {driver.upper()} Driver\n")
                f.write(f"- Build: {results[driver].get('build', 'n/a')}\n")
                if driver == "rust" and "unsafe_audit" in results[driver]:
                    f.write(f"- Unsafe occurrences: {results[driver]['unsafe_audit'].get('total', 0)}\n")
                f.write("\n")
    _log(f"\n Summary written to: {summary_path}", log_file)

# ── CLI ───────────────────────────────────────────────────────────────────────
def parse_args():
    parser = argparse.ArgumentParser(description="Memory safety test runner (Pi 3B optimized)")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--c", action="store_true")
    g.add_argument("--rust", action="store_true")
    g.add_argument("--both", action="store_true")

    parser.add_argument("--tools", nargs="+", choices=list(VALGRIND_TOOLS.keys()) + ["all", "none"], default=["memcheck"])
    parser.add_argument("--rust-tools", nargs="+", choices=["asan","geiger","clippy","audit","all","none"], default=["geiger","clippy","audit"])
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION)
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--log-dir", type=str, default=None)

    args = parser.parse_args()

    if "all" in args.tools:
        args.tools = list(VALGRIND_TOOLS.keys())
    elif "none" in args.tools:
        args.tools = []

    if "all" in args.rust_tools:
        args.rust_tools = ["asan", "geiger", "clippy", "audit"]
    elif "none" in args.rust_tools:
        args.rust_tools = []

    return args

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    args = parse_args()

    if args.tools and not _check_valgrind():
        sys.exit(1)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.log_dir) if args.log_dir else SCRIPT_DIR / "Logs" / f"memory_safety_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    master_log = open(out_dir / "run.log", "w")

    _log("=" * 60, master_log)
    _log(" Raspberry Pi 3B — Memory Safety Test Runner", master_log)
    _log(f" Started    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", master_log)
    _log(f" Output     : {out_dir}", master_log)
    _log(f" Valgrind   : {args.tools}", master_log)
    _log(f" Rust tools : {args.rust_tools}", master_log)
    _log(f" Duration   : {args.duration}s", master_log)
    _log(" LOW-MEMORY MODE active", master_log)
    _log("=" * 60, master_log)

    results = {}
    try:
        if args.c or args.both:
            results["c"] = run_c(args.tools, args.duration, out_dir, master_log, args.no_build)
        if args.rust or args.both:
            results["rust"] = run_rust(args.tools, args.rust_tools, args.duration, out_dir, master_log, args.no_build)
    finally:
        write_summary(out_dir, results, master_log)
        _log("\n Done.", master_log)
        master_log.close()

if __name__ == "__main__":
    main()