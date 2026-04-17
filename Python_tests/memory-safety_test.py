#!/usr/bin/env python3
"""
Memory Safety Test Runner for Raspberry Pi 3
Runs memory-safety analysis on the C and/or Rust BME280 drivers.

Tools (C driver)
----------------
  memcheck  — invalid reads/writes, use-after-free, double-free, uninitialised
              values, definitely/indirectly/possibly/still-reachable leak summary
  helgrind  — data races and lock-order violations (cheap sanity check for
              single-threaded drivers)
  massif    — heap allocation profile over time (peak heap, allocation sites)

Tools (Rust driver)
-------------------
  memcheck  — same as above; Valgrind runs on Rust release binaries and
              catches leaks/invalid access in unsafe FFI code
  asan      — AddressSanitizer via nightly Rust (`-Z sanitizer=address`).
              Detects buffer overflows, use-after-free, and related UB that
              static analysis cannot see. The closest available substitute
              for Miri, which does NOT work here — Miri cannot execute FFI
              calls (ioctl, open, read, write), and this driver is entirely
              FFI. This is worth citing in the Method chapter.
  geiger    — `cargo geiger`: counts `unsafe` usage across the crate and
              all transitive dependencies. Gives a richer picture than a
              local grep of src/.
  clippy    — `cargo clippy` with pedantic + restriction lints, filtered to
              unsafe-related rules. Flags undocumented unsafe and other
              suspicious patterns.
  audit     — `cargo audit`: scans dependencies against the RustSec
              advisory database for known vulnerabilities.
  unsafe_audit — static walk of src/*.rs producing a Markdown audit stub
              with file/line/context for manual annotation in the thesis.

Design notes
------------
* Valgrind slows the driver by 10-50x on ARMv7; ASan adds ~2x. Latency
  and throughput collected here are NOT representative — use
  run_drivers.py for performance measurement.
* Runs are duration-based (`--duration`), not sample-based, because
  per-reading overhead varies wildly under different instrumentation.
* System cleanup (cache drop, CPU governor) is intentionally omitted:
  memory-safety runs don't need timing consistency, and the
  instrumentation already perturbs the system far more than a dirty
  page cache would.

Assumed folder layout (relative to this script's location):
  ../C_bare-bones_driver_no_log/ <- C source files
  ../Rust_driver_no_log/         <- Rust project (has Cargo.toml, src/)
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

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_DURATION = 60          # seconds to run the driver under Valgrind
SHUTDOWN_GRACE = 5             # seconds to wait for the driver to flush leak summary
VALGRIND_STARTUP_BUDGET = 30   # extra seconds allowed for Valgrind to start up

VALGRIND_TOOLS = {
    "memcheck": [
        "--tool=memcheck",
        "--leak-check=full",
        "--show-leak-kinds=all",
        "--track-origins=yes",
        "--errors-for-leak-kinds=definite,indirect",
        "--error-exitcode=0",   # we parse the log; don't fail on errors found
        "--child-silent-after-fork=yes",
    ],
    "helgrind": [
        "--tool=helgrind",
        "--history-level=full",
        "--error-exitcode=0",
    ],
    "massif": [
        "--tool=massif",
        "--pages-as-heap=no",
        "--detailed-freq=1",
    ],
}


# ── Helpers ───────────────────────────────────────────────────────────────────
def _log(msg: str, log_file) -> None:
    print(msg)
    if log_file:
        log_file.write(msg.rstrip() + "\n")
        log_file.flush()


def _check_valgrind() -> bool:
    try:
        r = subprocess.run(
            ["valgrind", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            print(f" valgrind detected: {r.stdout.strip()}")
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    print(" x valgrind not found. Install with: sudo apt install valgrind")
    return False


def _build_c(log_file) -> bool:
    """
    Rebuild the C driver with debug info and no optimisation so that
    Valgrind reports map cleanly to source lines.
    """
    _log("\n [build] Compiling C driver with -g -O0 for Valgrind...", log_file)
    try:
        r = subprocess.run(
            [
                "gcc", "-std=c11", "-D_DEFAULT_SOURCE",
                "-Wall", "-Wextra", "-g", "-O0",
                "main.c", "bme280.c", "i2c_linux.c",
                "-o", C_BINARY,
            ],
            cwd=str(C_DIR),
            capture_output=True, text=True, timeout=60,
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
    """
    Rust release build with debug symbols. Requires Cargo.toml to have:
        [profile.release]
        debug = true
    If it doesn't, Valgrind will still run but symbols will be poor.
    """
    _log("\n [build] Compiling Rust driver (release with debug symbols)...", log_file)
    try:
        r = subprocess.run(
            ["cargo", "build", "--release"],
            cwd=str(RUST_DIR),
            capture_output=True, text=True, timeout=300,
        )
        if log_file:
            log_file.write(r.stdout + r.stderr)
        if r.returncode != 0:
            _log(f" x Rust build failed:\n{r.stderr}", log_file)
            return False
        _log(" [build] Rust build OK.", log_file)
        # Warn if debug symbols are likely missing
        cargo_toml = (RUST_DIR / "Cargo.toml").read_text(errors="ignore")
        if not re.search(r"\[profile\.release\][^\[]*\bdebug\s*=\s*(true|1|2)",
                         cargo_toml, re.DOTALL):
            _log(
                " [build] NOTE: Cargo.toml does not set `debug = true` under "
                "[profile.release]. Valgrind output will have poor symbols. "
                "Add this to Cargo.toml for better reports:\n"
                "   [profile.release]\n"
                "   debug = true",
                log_file,
            )
        return True
    except Exception as e:
        _log(f" x Rust build error: {e}", log_file)
        return False


# ── Rust tooling detection ────────────────────────────────────────────────────
def _which(cmd: str) -> bool:
    try:
        r = subprocess.run(["which", cmd], capture_output=True, text=True, timeout=3)
        return r.returncode == 0
    except Exception:
        return False


def _have_rust_nightly() -> bool:
    """Check whether a nightly toolchain is available for ASan."""
    try:
        r = subprocess.run(
            ["rustup", "toolchain", "list"],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0 and "nightly" in r.stdout
    except Exception:
        return False


def _have_cargo_subcommand(name: str) -> bool:
    """Check whether e.g. `cargo geiger` or `cargo audit` is installed."""
    try:
        r = subprocess.run(
            ["cargo", "--list"],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0 and re.search(
            rf"^\s*{re.escape(name)}\b", r.stdout, re.MULTILINE
        ) is not None
    except Exception:
        return False


# ── Rust: AddressSanitizer ────────────────────────────────────────────────────
# Pi 3 is armv7-unknown-linux-gnueabihf; ASan needs the sanitizer runtime
# for the target triple, which is shipped with rust-src on nightly.
ASAN_TARGET = "armv7-unknown-linux-gnueabihf"


def build_rust_asan(out_dir: Path, log_file) -> Path | None:
    """
    Build the Rust driver with AddressSanitizer enabled. Returns the binary
    path, or None if ASan is unavailable / build failed.

    This uses `cargo +nightly build` with `-Z build-std` so the sanitizer
    runtime is linked into std as well. If any of that is missing we
    skip gracefully.
    """
    _log("\n [asan] Preparing AddressSanitizer build (nightly)...", log_file)
    if not _have_rust_nightly():
        _log(
            " [asan] Skipped: no nightly toolchain detected.\n"
            "        Install with: rustup toolchain install nightly\n"
            "        And: rustup component add rust-src --toolchain nightly",
            log_file,
        )
        return None

    env = os.environ.copy()
    # -Zsanitizer=address turns on ASan; frame-pointer helps symbolication.
    env["RUSTFLAGS"] = (
        env.get("RUSTFLAGS", "") + " -Z sanitizer=address -C force-frame-pointers=yes"
    ).strip()

    cmd = [
        "cargo", "+nightly", "build",
        "--release",
        "-Z", "build-std",
        "--target", ASAN_TARGET,
    ]
    _log(f" [asan] {' '.join(cmd)}", log_file)
    try:
        r = subprocess.run(
            cmd, cwd=str(RUST_DIR),
            env=env,
            capture_output=True, text=True, timeout=900,
        )
    except Exception as e:
        _log(f" [asan] Build error: {e}", log_file)
        return None

    if log_file:
        log_file.write(r.stdout + r.stderr)
    if r.returncode != 0:
        _log(
            " [asan] Build failed. Common causes:\n"
            "  - `rust-src` component not installed on nightly\n"
            "  - Target not installed: rustup target add " + ASAN_TARGET + "\n"
            "  - ASan runtime not available for armv7 (older nightlies)\n"
            "Skipping ASan run.",
            log_file,
        )
        return None

    binary = (
        RUST_DIR / "target" / ASAN_TARGET / "release" / "bme280_bare_bones"
    )
    if not binary.exists():
        _log(f" [asan] Build reported OK but binary not found at {binary}", log_file)
        return None
    _log(f" [asan] Built: {binary}", log_file)
    return binary


def run_rust_asan(binary: Path, duration: int, out_dir: Path, log_file) -> dict:
    """
    Run the ASan-instrumented Rust binary for `duration` seconds, capture
    its stderr (where ASan writes reports), then parse counts.
    """
    asan_log = out_dir / "rust_driver_asan.log"
    stdout_log = out_dir / "rust_driver_asan.stdout.log"
    _log(f"\n{'─' * 60}", log_file)
    _log(f" [rust/asan] Running {binary} for {duration}s", log_file)

    # ASan options:
    #   detect_leaks=1           — enable LeakSanitizer at exit
    #   halt_on_error=0          — keep running so we see multiple issues
    #   abort_on_error=0         — exit cleanly, write report
    #   symbolize=1              — resolve symbols in the report
    env = os.environ.copy()
    env["ASAN_OPTIONS"] = (
        "detect_leaks=1:halt_on_error=0:abort_on_error=0:symbolize=1"
        f":log_path={asan_log}"
    )

    proc = subprocess.Popen(
        [str(binary), "0x76", "0"],
        cwd=str(RUST_DIR),
        stdout=open(stdout_log, "w"),
        stderr=subprocess.STDOUT,
        env=env,
        preexec_fn=os.setsid,
    )
    try:
        time.sleep(duration)
        _log(" [rust/asan] Sending SIGINT...", log_file)
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGINT)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=SHUTDOWN_GRACE)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                pass
            proc.wait()
    except KeyboardInterrupt:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass
        proc.wait()

    # ASan writes to <log_path>.<pid>; collect them all into one file.
    combined = out_dir / "rust_driver_asan.combined.log"
    pieces = sorted(out_dir.glob("rust_driver_asan.log.*"))
    with open(combined, "w") as f:
        for p in pieces:
            f.write(f"=== {p.name} ===\n")
            f.write(p.read_text(errors="ignore"))
            f.write("\n")

    stats = parse_asan_log(combined)
    stats["log_path"] = str(combined)
    _log(f" [rust/asan] Log: {combined}", log_file)
    _render_stats("rust_driver", "asan", stats, log_file)
    return stats


def parse_asan_log(log_path: Path) -> dict:
    """Count ASan error categories from one combined log file."""
    if not log_path.exists() or log_path.stat().st_size == 0:
        return {"errors": 0, "note": "no ASan output (clean run or no instrumentation triggered)"}

    text = log_path.read_text(errors="ignore")
    patterns = {
        "heap_buffer_overflow":   r"heap-buffer-overflow",
        "stack_buffer_overflow":  r"stack-buffer-overflow",
        "global_buffer_overflow": r"global-buffer-overflow",
        "heap_use_after_free":    r"heap-use-after-free",
        "double_free":            r"double-free|attempting double-free",
        "invalid_free":           r"attempting free on address which was not malloc",
        "memory_leaks":           r"detected memory leaks|LeakSanitizer: detected memory leaks",
        "unknown_errors":         r"AddressSanitizer: [A-Za-z0-9_-]+",
    }
    stats = {}
    for k, pat in patterns.items():
        stats[k] = len(re.findall(pat, text, re.IGNORECASE))
    # Total unique error reports (ASan marks them with "ERROR: AddressSanitizer")
    stats["errors"] = len(re.findall(r"ERROR: AddressSanitizer", text))
    return stats


# ── Rust: cargo geiger (unsafe usage across deps) ─────────────────────────────
def run_cargo_geiger(out_dir: Path, log_file) -> dict:
    """
    Run `cargo geiger` to count unsafe items in the crate and its
    transitive dependencies. Installs it if missing? No — we just skip;
    installing takes minutes on a Pi 3 and should be an explicit user step.
    """
    if not _have_cargo_subcommand("geiger"):
        _log(
            "\n [geiger] Skipped: `cargo geiger` not installed.\n"
            "          Install with: cargo install cargo-geiger",
            log_file,
        )
        return {"status": "skipped"}

    log_path = out_dir / "cargo_geiger.log"
    _log("\n [geiger] Running cargo geiger (this may take several minutes)...", log_file)
    try:
        r = subprocess.run(
            ["cargo", "geiger", "--output-format", "Ascii"],
            cwd=str(RUST_DIR),
            capture_output=True, text=True, timeout=1800,
        )
    except subprocess.TimeoutExpired:
        _log(" [geiger] Timed out after 30 minutes.", log_file)
        return {"status": "timeout"}

    log_path.write_text(r.stdout + "\n=== STDERR ===\n" + r.stderr)
    if r.returncode != 0:
        _log(f" [geiger] Exited with code {r.returncode}. See {log_path}", log_file)
        return {"status": "failed", "log_path": str(log_path)}

    # Parse geiger's summary table. Format (simplified):
    #   Metric output format: x/y
    #       x = unsafe used by the build
    #       y = total unsafe found in the crate
    stats = {"status": "ok", "log_path": str(log_path)}
    # Per-category totals appear in the final "Totals:" section.
    totals = re.search(
        r"(\d+)/(\d+)\s+functions\s*\n\s*(\d+)/(\d+)\s+expressions\s*\n"
        r"\s*(\d+)/(\d+)\s+impls\s*\n\s*(\d+)/(\d+)\s+traits\s*\n"
        r"\s*(\d+)/(\d+)\s+methods",
        r.stdout,
    )
    if totals:
        g = [int(x) for x in totals.groups()]
        stats.update({
            "unsafe_functions_used":   g[0], "unsafe_functions_total":   g[1],
            "unsafe_expressions_used": g[2], "unsafe_expressions_total": g[3],
            "unsafe_impls_used":       g[4], "unsafe_impls_total":       g[5],
            "unsafe_traits_used":      g[6], "unsafe_traits_total":      g[7],
            "unsafe_methods_used":     g[8], "unsafe_methods_total":     g[9],
        })
    _log(f" [geiger] Log: {log_path}", log_file)
    for k, v in stats.items():
        if k not in ("log_path", "status"):
            _log(f"   {k:<28s} : {v}", log_file)
    return stats


# ── Rust: clippy (unsafe-focused lints) ───────────────────────────────────────
CLIPPY_UNSAFE_LINTS = [
    # Restriction lints that target unsafe code quality
    "clippy::undocumented_unsafe_blocks",
    "clippy::multiple_unsafe_ops_per_block",
    "clippy::missing_safety_doc",
    "clippy::unnecessary_safety_comment",
    "clippy::unnecessary_safety_doc",
    # Pedantic lints that often flag hardware-driver anti-patterns
    "clippy::cast_ptr_alignment",
    "clippy::ptr_as_ptr",
    "clippy::transmute_ptr_to_ref",
]


def run_cargo_clippy(out_dir: Path, log_file) -> dict:
    """Run clippy with unsafe-focused lints escalated to warnings."""
    log_path = out_dir / "cargo_clippy.log"
    _log("\n [clippy] Running cargo clippy with unsafe-focused lints...", log_file)

    args = ["cargo", "clippy", "--release", "--all-targets", "--", "-A", "clippy::all"]
    for lint in CLIPPY_UNSAFE_LINTS:
        args += ["-W", lint]

    try:
        r = subprocess.run(
            args, cwd=str(RUST_DIR),
            capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        _log(" [clippy] Timed out after 10 minutes.", log_file)
        return {"status": "timeout"}

    log_path.write_text(r.stdout + "\n=== STDERR ===\n" + r.stderr)
    # Clippy warnings appear in stderr; count them by lint.
    warnings_by_lint = {}
    for lint in CLIPPY_UNSAFE_LINTS:
        warnings_by_lint[lint] = len(re.findall(re.escape(lint), r.stderr))
    total = sum(warnings_by_lint.values())
    stats = {
        "status": "ok" if r.returncode == 0 else f"rc={r.returncode}",
        "log_path": str(log_path),
        "total_warnings": total,
        "by_lint": warnings_by_lint,
    }
    _log(f" [clippy] Log: {log_path}", log_file)
    _log(f"   total_warnings: {total}", log_file)
    for lint, n in warnings_by_lint.items():
        if n:
            _log(f"   {lint:<45s} : {n}", log_file)
    return stats


# ── Rust: cargo audit (RustSec advisory DB) ───────────────────────────────────
def run_cargo_audit(out_dir: Path, log_file) -> dict:
    if not _have_cargo_subcommand("audit"):
        _log(
            "\n [audit] Skipped: `cargo audit` not installed.\n"
            "         Install with: cargo install cargo-audit",
            log_file,
        )
        return {"status": "skipped"}

    log_path = out_dir / "cargo_audit.log"
    _log("\n [audit] Running cargo audit...", log_file)
    try:
        r = subprocess.run(
            ["cargo", "audit", "--color", "never"],
            cwd=str(RUST_DIR),
            capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        _log(" [audit] Timed out after 5 minutes.", log_file)
        return {"status": "timeout"}

    log_path.write_text(r.stdout + "\n=== STDERR ===\n" + r.stderr)
    # cargo-audit summary line example:
    #   "Crate: foo  Version: 0.1.0  Title: ..."
    #   "error: 2 vulnerabilities found!"
    m_vuln = re.search(r"(\d+)\s+vulnerabilit", r.stdout + r.stderr)
    m_warn = re.search(r"(\d+)\s+(?:warnings?|allowed)", r.stdout + r.stderr)
    stats = {
        "status": "ok",
        "log_path": str(log_path),
        "vulnerabilities": int(m_vuln.group(1)) if m_vuln else 0,
        "warnings": int(m_warn.group(1)) if m_warn else 0,
    }
    _log(f" [audit] Log: {log_path}", log_file)
    for k, v in stats.items():
        if k not in ("log_path", "status"):
            _log(f"   {k:<20s} : {v}", log_file)
    return stats


# ── Valgrind runner ───────────────────────────────────────────────────────────
def run_under_valgrind(
    label: str,
    driver_cmd: list,
    driver_cwd: Path,
    tool: str,
    duration: int,
    out_dir: Path,
    log_file,
) -> dict:
    """
    Run `driver_cmd` under `valgrind --tool=<tool>` for `duration` seconds,
    then send SIGINT so the driver can exit cleanly and Valgrind can emit its
    summary. Returns a small dict of extracted stats.
    """
    tool_args = VALGRIND_TOOLS[tool]
    valgrind_log = out_dir / f"{label}_{tool}.valgrind.log"

    cmd = ["valgrind", f"--log-file={valgrind_log}"] + list(tool_args)
    if tool == "massif":
        cmd.append(f"--massif-out-file={out_dir / f'{label}_massif.out'}")
    cmd += [str(c) for c in driver_cmd]

    _log(f"\n{'─' * 60}", log_file)
    _log(f" [{label}/{tool}] {' '.join(cmd)}", log_file)
    _log(f" [{label}/{tool}] Duration: {duration}s "
         f"(+{VALGRIND_STARTUP_BUDGET}s startup, +{SHUTDOWN_GRACE}s shutdown)", log_file)

    driver_stdout = out_dir / f"{label}_{tool}.stdout.log"
    start = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        cwd=str(driver_cwd),
        stdout=open(driver_stdout, "w"),
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )

    try:
        # Give Valgrind time to start and the driver time to do work.
        time.sleep(duration + VALGRIND_STARTUP_BUDGET)
        _log(f" [{label}/{tool}] Sending SIGINT for clean shutdown...", log_file)
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGINT)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=SHUTDOWN_GRACE)
        except subprocess.TimeoutExpired:
            _log(f" [{label}/{tool}] SIGINT ignored, escalating to SIGTERM...", log_file)
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=SHUTDOWN_GRACE)
            except Exception:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    pass
                proc.wait()
    except KeyboardInterrupt:
        _log(" [!] Interrupted — killing valgrind process group.", log_file)
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass
        proc.wait()

    elapsed = time.monotonic() - start
    _log(f" [{label}/{tool}] Done in {elapsed:.1f}s.", log_file)
    _log(f" [{label}/{tool}] Valgrind log: {valgrind_log}", log_file)

    # Parse the log for the stats the thesis needs.
    stats = parse_valgrind_log(valgrind_log, tool)
    _render_stats(label, tool, stats, log_file)
    return stats


def parse_valgrind_log(log_path: Path, tool: str) -> dict:
    """Pull the numbers the thesis table needs out of the Valgrind log."""
    if not log_path.exists():
        return {"error": "log file missing"}

    text = log_path.read_text(errors="ignore")
    stats: dict = {"log_path": str(log_path)}

    # Total error count line, e.g.:
    # "ERROR SUMMARY: 0 errors from 0 contexts (suppressed: 0 from 0)"
    m = re.search(r"ERROR SUMMARY:\s+(\d+)\s+errors?\s+from\s+(\d+)\s+contexts?", text)
    if m:
        stats["errors"] = int(m.group(1))
        stats["error_contexts"] = int(m.group(2))

    if tool == "memcheck":
        # Leak summary lines
        for kind in ("definitely lost", "indirectly lost",
                     "possibly lost", "still reachable", "suppressed"):
            m = re.search(
                rf"{re.escape(kind)}:\s+([\d,]+)\s+bytes\s+in\s+([\d,]+)\s+blocks?",
                text,
            )
            if m:
                key = kind.replace(" ", "_")
                stats[f"{key}_bytes"] = int(m.group(1).replace(",", ""))
                stats[f"{key}_blocks"] = int(m.group(2).replace(",", ""))

        # Count each type of invalid access, uninitialised value, free error.
        patterns = {
            "invalid_read": r"Invalid read of size",
            "invalid_write": r"Invalid write of size",
            "invalid_free": r"Invalid free",
            "mismatched_free": r"Mismatched free",
            "uninitialised_value": r"Conditional jump or move depends on uninitialised value",
            "uninitialised_syscall": r"Syscall param .* uninitialised",
        }
        for key, pat in patterns.items():
            stats[key] = len(re.findall(pat, text))

    if tool == "helgrind":
        patterns = {
            "data_races": r"Possible data race",
            "lock_order": r"lock order",
        }
        for key, pat in patterns.items():
            stats[key] = len(re.findall(pat, text, re.IGNORECASE))

    return stats


def _render_stats(label: str, tool: str, stats: dict, log_file) -> None:
    _log(f"\n [{label}/{tool}] Parsed results:", log_file)
    if not stats or stats.get("error"):
        _log(f"   (no stats extracted: {stats.get('error', 'unknown')})", log_file)
        return
    for k, v in stats.items():
        if k == "log_path":
            continue
        _log(f"   {k:<28s} : {v}", log_file)


# ── Rust unsafe-block audit ───────────────────────────────────────────────────
UNSAFE_BLOCK_RE = re.compile(r"\bunsafe\s*(?:fn\b|impl\b|trait\b|\{)")


def audit_rust_unsafe(out_dir: Path, log_file) -> dict:
    """
    Walk the Rust driver's source tree, count `unsafe` occurrences, extract
    each block's context, and write a Markdown audit stub. The author fills
    in the "Purpose" and "Justification" fields by hand.
    """
    if not RUST_SRC_DIR.exists():
        _log(f" [audit] Rust src dir not found: {RUST_SRC_DIR}", log_file)
        return {}

    findings: list[dict] = []
    by_kind = {"unsafe fn": 0, "unsafe impl": 0, "unsafe trait": 0, "unsafe block": 0}

    for rs_file in sorted(RUST_SRC_DIR.rglob("*.rs")):
        try:
            lines = rs_file.read_text(errors="ignore").splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines):
            if not UNSAFE_BLOCK_RE.search(line):
                continue
            if re.search(r"\bunsafe\s+fn\b", line):
                kind = "unsafe fn"
            elif re.search(r"\bunsafe\s+impl\b", line):
                kind = "unsafe impl"
            elif re.search(r"\bunsafe\s+trait\b", line):
                kind = "unsafe trait"
            else:
                kind = "unsafe block"
            by_kind[kind] += 1
            ctx_start = max(0, i - 2)
            ctx_end = min(len(lines), i + 8)
            findings.append({
                "file": str(rs_file.relative_to(RUST_DIR)),
                "line": i + 1,
                "kind": kind,
                "context": "\n".join(lines[ctx_start:ctx_end]),
            })

    report_path = out_dir / "rust_unsafe_audit.md"
    with open(report_path, "w") as f:
        f.write("# Rust `unsafe` Audit\n\n")
        f.write(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n\n")
        f.write("## Summary\n\n")
        f.write(f"- Total `unsafe` occurrences: **{len(findings)}**\n")
        for k, v in by_kind.items():
            f.write(f"- `{k}`: {v}\n")
        f.write("\n## Findings\n\n")
        f.write("Fill in **Purpose** and **Justification** for each entry.\n\n")
        for n, fnd in enumerate(findings, 1):
            f.write(f"### {n}. `{fnd['kind']}` — `{fnd['file']}:{fnd['line']}`\n\n")
            f.write("**Purpose:** _TODO — what hardware/FFI interaction does this perform?_\n\n")
            f.write("**Justification:** _TODO — why can't this be done in safe Rust?_\n\n")
            f.write("```rust\n")
            f.write(fnd["context"])
            f.write("\n```\n\n")

    _log(f"\n [audit] Rust unsafe summary: {len(findings)} occurrence(s) "
         f"across {len({f['file'] for f in findings})} file(s).", log_file)
    for k, v in by_kind.items():
        _log(f"   {k:<15s} : {v}", log_file)
    _log(f" [audit] Audit stub written to: {report_path}", log_file)

    return {
        "total": len(findings),
        "by_kind": by_kind,
        "report_path": str(report_path),
    }


# ── Top-level orchestration ───────────────────────────────────────────────────
def run_c(tools: list, duration: int, out_dir: Path, log_file, skip_build: bool) -> dict:
    _log("\n" + "=" * 60, log_file)
    _log(" C Driver — Memory Safety Analysis", log_file)
    _log("=" * 60, log_file)
    if not skip_build and not _build_c(log_file):
        return {"build": "failed"}

    c_out = out_dir / "C"
    c_out.mkdir(parents=True, exist_ok=True)
    driver_cmd = [f"./{C_BINARY}", "0x76", "0"]
    results = {"build": "ok" if not skip_build else "skipped"}
    for tool in tools:
        results[tool] = run_under_valgrind(
            label="c_driver",
            driver_cmd=driver_cmd,
            driver_cwd=C_DIR,
            tool=tool,
            duration=duration,
            out_dir=c_out,
            log_file=log_file,
        )
    return results


def run_rust(tools: list, rust_tools: list, duration: int, out_dir: Path,
             log_file, skip_build: bool) -> dict:
    _log("\n" + "=" * 60, log_file)
    _log(" Rust Driver — Memory Safety Analysis", log_file)
    _log("=" * 60, log_file)
    if not skip_build and not _build_rust(log_file):
        return {"build": "failed"}

    rust_out = out_dir / "Rust"
    rust_out.mkdir(parents=True, exist_ok=True)

    # Static audit is cheap and always useful.
    audit = audit_rust_unsafe(rust_out, log_file)
    results = {
        "build": "ok" if not skip_build else "skipped",
        "unsafe_audit": audit,
    }

    # Valgrind tools (same suite as the C driver).
    driver_cmd = [str(RUST_BINARY), "0x76", "0"]
    for tool in tools:
        results[tool] = run_under_valgrind(
            label="rust_driver",
            driver_cmd=driver_cmd,
            driver_cwd=RUST_DIR,
            tool=tool,
            duration=duration,
            out_dir=rust_out,
            log_file=log_file,
        )

    # Rust-specific tools.
    if "asan" in rust_tools:
        asan_bin = build_rust_asan(rust_out, log_file)
        if asan_bin:
            results["asan"] = run_rust_asan(asan_bin, duration, rust_out, log_file)
        else:
            results["asan"] = {"status": "skipped"}
    if "geiger" in rust_tools:
        results["geiger"] = run_cargo_geiger(rust_out, log_file)
    if "clippy" in rust_tools:
        results["clippy"] = run_cargo_clippy(rust_out, log_file)
    if "audit" in rust_tools:
        results["audit"] = run_cargo_audit(rust_out, log_file)

    return results


# ── Summary report ────────────────────────────────────────────────────────────
def write_summary(out_dir: Path, results: dict, log_file) -> None:
    summary_path = out_dir / "summary.md"
    with open(summary_path, "w") as f:
        f.write("# Memory Safety Analysis — Summary\n\n")
        f.write(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n\n")

        for driver_name in ("C", "Rust"):
            key = driver_name.lower()
            if key not in results:
                continue
            f.write(f"## {driver_name} Driver\n\n")
            r = results[key]
            f.write(f"- Build: `{r.get('build', 'n/a')}`\n")
            if driver_name == "Rust" and "unsafe_audit" in r:
                a = r["unsafe_audit"]
                f.write(f"- `unsafe` occurrences (local src/): **{a.get('total', 0)}**\n")
                for k, v in a.get("by_kind", {}).items():
                    f.write(f"  - `{k}`: {v}\n")

            # Valgrind tools
            for tool in ("memcheck", "helgrind", "massif"):
                if tool not in r:
                    continue
                f.write(f"\n### {tool}\n\n")
                for k, v in r[tool].items():
                    if k == "log_path":
                        f.write(f"- Log: `{v}`\n")
                    else:
                        f.write(f"- {k}: `{v}`\n")

            # Rust-specific tools
            if driver_name == "Rust":
                if "asan" in r:
                    f.write("\n### AddressSanitizer\n\n")
                    for k, v in r["asan"].items():
                        if k == "log_path":
                            f.write(f"- Log: `{v}`\n")
                        else:
                            f.write(f"- {k}: `{v}`\n")
                if "geiger" in r:
                    f.write("\n### cargo geiger (unsafe across deps)\n\n")
                    for k, v in r["geiger"].items():
                        f.write(f"- {k}: `{v}`\n")
                if "clippy" in r:
                    f.write("\n### cargo clippy (unsafe-focused lints)\n\n")
                    g = r["clippy"]
                    f.write(f"- Total warnings: `{g.get('total_warnings', 0)}`\n")
                    f.write(f"- Log: `{g.get('log_path', 'n/a')}`\n")
                    for lint, n in g.get("by_lint", {}).items():
                        f.write(f"  - `{lint}`: {n}\n")
                if "audit" in r:
                    f.write("\n### cargo audit (RustSec advisory DB)\n\n")
                    for k, v in r["audit"].items():
                        f.write(f"- {k}: `{v}`\n")
            f.write("\n")

        f.write("## Thesis Metrics Mapping\n\n")
        f.write("| Thesis metric | Source |\n")
        f.write("|---|---|\n")
        f.write("| Memory leaks (C) | memcheck `definitely_lost_*`, `indirectly_lost_*` |\n")
        f.write("| Memory leaks (Rust) | memcheck same fields + ASan `memory_leaks` |\n")
        f.write("| Invalid memory accesses (C) | memcheck `invalid_read`, `invalid_write` |\n")
        f.write("| Invalid memory accesses (Rust) | memcheck same + ASan `heap_use_after_free`, `*_buffer_overflow` |\n")
        f.write("| Buffer overflow incidents | memcheck heap/stack invalid access + ASan `*_buffer_overflow` |\n")
        f.write("| Number of `unsafe` blocks (Rust, local) | `unsafe_audit.total` |\n")
        f.write("| `unsafe` usage across deps (Rust) | `geiger.unsafe_*_used/total` |\n")
        f.write("| Undocumented unsafe (Rust) | clippy `undocumented_unsafe_blocks` |\n")
        f.write("| Known-vulnerable deps (Rust) | cargo-audit `vulnerabilities` |\n")

    _log(f"\n Summary written to: {summary_path}", log_file)


# ── CLI ───────────────────────────────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Memory safety analysis for C/Rust BME280 drivers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full analysis on both drivers (all Valgrind + all Rust tools)
  sudo python3 run_memory_safety.py --both --tools all --rust-tools all

  # Just memcheck on C, 2 minutes
  python3 run_memory_safety.py --c --tools memcheck --duration 120

  # Rust: skip Valgrind, use the Rust-native toolchain only
  python3 run_memory_safety.py --rust --tools none \\
      --rust-tools asan geiger clippy audit

  # Cheap pre-thesis sanity check: static-only, no runs at all
  python3 run_memory_safety.py --rust --tools none \\
      --rust-tools geiger clippy audit

Notes:
  * Valgrind slows the driver by 10-50x; ASan adds ~2x. Timing data is
    NOT collected here — use run_drivers.py for performance.
  * i2c-dev access usually requires root or 'i2c' group membership. If
    the driver needs root, run this script with sudo.
  * Miri is NOT included: it cannot execute FFI/syscalls and the driver
    is entirely FFI. AddressSanitizer is the runtime substitute.

Rust-tool prerequisites (installed separately):
  asan    : rustup toolchain install nightly
            rustup component add rust-src --toolchain nightly
            rustup target add armv7-unknown-linux-gnueabihf
  geiger  : cargo install cargo-geiger
  audit   : cargo install cargo-audit
  clippy  : rustup component add clippy
        """,
    )
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--c", action="store_true", help="Analyse the C driver only")
    g.add_argument("--rust", action="store_true", help="Analyse the Rust driver only")
    g.add_argument("--both", action="store_true", help="Analyse both drivers")

    parser.add_argument(
        "--tools", nargs="+",
        choices=list(VALGRIND_TOOLS.keys()) + ["all", "none"],
        default=["memcheck"],
        help="Which Valgrind tools to run (default: memcheck). "
             "'all' = memcheck+helgrind+massif. 'none' = skip Valgrind.",
    )
    parser.add_argument(
        "--rust-tools", nargs="+",
        choices=["asan", "geiger", "clippy", "audit", "all", "none"],
        default=["none"],
        help="Which Rust-native tools to run (default: none). "
             "'all' = asan+geiger+clippy+audit. Only applies when --rust or --both.",
    )
    parser.add_argument(
        "--duration", type=int, default=DEFAULT_DURATION, metavar="SECONDS",
        help=f"How long each runtime analysis executes the driver "
             f"(default: {DEFAULT_DURATION}s)",
    )
    parser.add_argument(
        "--no-build", action="store_true",
        help="Skip rebuilding; use existing binaries as-is",
    )
    parser.add_argument(
        "--log-dir", type=str, default=None, metavar="PATH",
        help="Base directory for logs (default: ./Logs/memory_safety_<timestamp>)",
    )
    args = parser.parse_args()

    # Expand 'all' / 'none' for Valgrind tools.
    if "all" in args.tools:
        args.tools = list(VALGRIND_TOOLS.keys())
    elif "none" in args.tools:
        args.tools = []

    # Expand 'all' / 'none' for Rust tools.
    if "all" in args.rust_tools:
        args.rust_tools = ["asan", "geiger", "clippy", "audit"]
    elif "none" in args.rust_tools:
        args.rust_tools = []

    return args


def main() -> None:
    args = parse_args()

    # Don't require valgrind if the user explicitly asked for 'none'.
    if args.tools and not _check_valgrind():
        sys.exit(1)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = (Path(args.log_dir) if args.log_dir
               else SCRIPT_DIR / "Logs" / f"memory_safety_{timestamp}")
    out_dir.mkdir(parents=True, exist_ok=True)

    master_log = open(out_dir / "run.log", "w")

    _log("=" * 60, master_log)
    _log(" Raspberry Pi 3 — Memory Safety Test Runner", master_log)
    _log(f" Started    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", master_log)
    _log(f" Output     : {out_dir}", master_log)
    _log(f" Valgrind   : {args.tools if args.tools else '(disabled)'}", master_log)
    _log(f" Rust tools : {args.rust_tools if args.rust_tools else '(disabled)'}", master_log)
    _log(f" Duration   : {args.duration}s per runtime tool", master_log)
    _log(f" Build      : {'skipped' if args.no_build else 'enabled'}", master_log)
    _log("=" * 60, master_log)

    results: dict = {}
    try:
        if args.c or args.both:
            results["c"] = run_c(args.tools, args.duration, out_dir,
                                 master_log, args.no_build)
        if args.rust or args.both:
            results["rust"] = run_rust(
                args.tools, args.rust_tools, args.duration, out_dir,
                master_log, args.no_build,
            )
    finally:
        write_summary(out_dir, results, master_log)
        _log("\n" + "=" * 60, master_log)
        _log(" Done.", master_log)
        _log("=" * 60, master_log)
        master_log.close()


if __name__ == "__main__":
    main()