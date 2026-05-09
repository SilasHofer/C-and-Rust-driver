#!/usr/bin/env python3
"""
Sequential performance validation controller for C vs Rust drivers.

This script runs interleaved benchmark blocks using performance_test.py, then
applies a sequential stopping rule based on confidence intervals and a practical
minimum detectable effect (MDE).

Decision logic uses relative latency difference:
  rel_diff_pct = ((mean_c - mean_rust) / pooled_mean) * 100
where pooled_mean = (mean_c + mean_rust) / 2.

Stop when min samples are reached and one of these is true:
  1) rel CI entirely below -MDE: C is faster
  2) rel CI entirely above +MDE: Rust is faster
  3) rel CI entirely inside [-MDE, +MDE]: practically equivalent

If max samples are reached first, stop with an inconclusive/no-practical-
difference-at-cap result.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from statistics import NormalDist, fmean, variance

SCRIPT_DIR = Path(__file__).parent.resolve()
PERF_SCRIPT = SCRIPT_DIR / "performance_test.py"
LOG_C_DIR = SCRIPT_DIR / "Logs" / "Performance" / "C"
LOG_RUST_DIR = SCRIPT_DIR / "Logs" / "Performance" / "Rust"
OUT_DIR = SCRIPT_DIR / "Logs" / "Performance" / "Sequential"

LATENCY_RE = re.compile(r"lat=([0-9]*\.?[0-9]+)ms")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Sequential CI-based validation for C vs Rust latency",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--samples", type=int, default=500, help="Samples per run")
    p.add_argument("--block-runs", type=int, default=1, help="Runs per sequential look")
    p.add_argument("--min-samples", type=int, default=2000, help="Minimum latency points per driver before allowing stop")
    p.add_argument("--max-samples", type=int, default=10000, help="Maximum latency points per driver")
    p.add_argument("--mde-pct", type=float, default=1.0, help="Practical effect threshold (percent)")
    p.add_argument("--confidence", type=float, default=0.95, help="Two-sided confidence level")
    p.add_argument("--warmup", type=int, default=100, help="Warm-up readings forwarded to performance_test.py")
    p.add_argument("--timeout", type=int, default=60, help="Step timeout forwarded to performance_test.py")
    p.add_argument(
        "--min-looks",
        type=int,
        default=8,
        help="Minimum number of sequential looks before allowing a stop decision",
    )
    p.add_argument(
        "--stability-window",
        type=int,
        default=3,
        help="Number of recent looks used for consistency check",
    )
    p.add_argument(
        "--max-cv-change-pct",
        type=float,
        default=0.25,
        help="Max allowed CV variation across the stability window (percentage points)",
    )
    p.add_argument(
        "--build-each-block",
        action="store_true",
        help="Rebuild inside every block (slower). Default is run-only blocks.",
    )
    p.add_argument(
        "--fixed-order",
        action="store_true",
        help="Always run C then Rust each look. Default alternates order per look.",
    )
    p.add_argument("--label", type=str, default="", help="Optional label for output filenames")
    return p.parse_args()


def list_logs(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {p.name for p in path.glob("*.log")}


def parse_latency_ms(log_file: Path) -> list[float]:
    out: list[float] = []
    try:
        with log_file.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = LATENCY_RE.search(line)
                if m:
                    out.append(float(m.group(1)))
    except FileNotFoundError:
        return out
    return out


def mean_std(vals: list[float]) -> tuple[float, float]:
    if not vals:
        return 0.0, 0.0
    if len(vals) == 1:
        return vals[0], 0.0
    return fmean(vals), math.sqrt(variance(vals))


def diff_ci(c_vals: list[float], r_vals: list[float], conf: float) -> tuple[float, float, float]:
    """Return (diff_mean_ms, ci_low_ms, ci_high_ms) for diff = C - Rust."""
    mc, sc = mean_std(c_vals)
    mr, sr = mean_std(r_vals)
    nc = len(c_vals)
    nr = len(r_vals)

    diff = mc - mr
    if nc < 2 or nr < 2:
        return diff, diff, diff

    se = math.sqrt((sc * sc) / nc + (sr * sr) / nr)
    if se == 0:
        return diff, diff, diff

    z = NormalDist().inv_cdf(0.5 + conf / 2.0)
    margin = z * se
    return diff, diff - margin, diff + margin


def classify(rel_low: float, rel_high: float, mde_pct: float) -> str:
    if rel_high < -mde_pct:
        return "C_FASTER"
    if rel_low > mde_pct:
        return "RUST_FASTER"
    if rel_low >= -mde_pct and rel_high <= mde_pct:
        return "PRACTICALLY_EQUIVALENT"
    return "CONTINUE"


def cv_pct(mean_ms: float, std_ms: float) -> float:
    if mean_ms <= 0:
        return 0.0
    return (std_ms / mean_ms) * 100.0


def is_stable(looks: list[dict], window: int, max_cv_change_pct: float) -> tuple[bool, str]:
    if window <= 1:
        return True, "stability-window<=1"
    if len(looks) < window:
        return False, f"need {window} looks for stability, have {len(looks)}"

    recent = looks[-window:]
    c_vals = [r["cv_c_pct"] for r in recent]
    r_vals = [r["cv_rust_pct"] for r in recent]

    c_change = max(c_vals) - min(c_vals)
    r_change = max(r_vals) - min(r_vals)
    ok = c_change <= max_cv_change_pct and r_change <= max_cv_change_pct
    msg = (
        f"CV change recent{window}: "
        f"C={c_change:.4f}pp Rust={r_change:.4f}pp "
        f"(limit {max_cv_change_pct:.4f}pp)"
    )
    return ok, msg


def run_driver(args: argparse.Namespace, driver: str, log_base_dir: Path) -> int:
    if driver not in {"c", "rust"}:
        raise ValueError("driver must be 'c' or 'rust'")

    # Create subdirectory for this driver within the session log folder
    driver_log_dir = log_base_dir / ("C" if driver == "c" else "Rust")
    driver_log_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        str(PERF_SCRIPT),
        "--c" if driver == "c" else "--rust",
        "--runs",
        str(args.block_runs),
        "--samples",
        str(args.samples),
        "--warmup",
        str(args.warmup),
        "--timeout",
        str(args.timeout),
        "--log",
        "--log-dir",
        str(driver_log_dir),
    ]
    if not args.build_each_block:
        cmd.append("--no-build")
    print(f"\n[controller] Running {driver.upper()} block:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(SCRIPT_DIR))
    return result.returncode


def run_block(args: argparse.Namespace, look_idx: int, log_base_dir: Path) -> tuple[int, str]:
    if args.fixed_order:
        order = ["c", "rust"]
    else:
        order = ["c", "rust"] if look_idx % 2 == 1 else ["rust", "c"]

    order_label = "->".join(d.upper() for d in order)
    print(f"\n[controller] Look {look_idx} order: {order_label}")

    for driver in order:
        rc = run_driver(args, driver, log_base_dir)
        if rc != 0:
            return rc, order_label
    return 0, order_label


def main() -> None:
    args = parse_args()

    if not PERF_SCRIPT.exists():
        print(f"ERROR: Missing script: {PERF_SCRIPT}")
        sys.exit(2)
    if not (0.0 < args.confidence < 1.0):
        print("ERROR: --confidence must be between 0 and 1")
        sys.exit(2)
    if args.min_samples <= 0 or args.max_samples <= 0 or args.samples <= 1 or args.block_runs <= 0:
        print("ERROR: sample and run values must be positive, and --samples must be > 1")
        sys.exit(2)
    if args.min_samples > args.max_samples:
        print("ERROR: --min-samples cannot exceed --max-samples")
        sys.exit(2)
    if args.min_looks <= 0:
        print("ERROR: --min-looks must be >= 1")
        sys.exit(2)
    if args.stability_window <= 0:
        print("ERROR: --stability-window must be >= 1")
        sys.exit(2)
    if args.max_cv_change_pct < 0:
        print("ERROR: --max-cv-change-pct must be >= 0")
        sys.exit(2)

    # Create timestamped folder for this sequential validation session
    session_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    session_folder = SCRIPT_DIR / "Logs" / f"sequential_{session_timestamp}"
    session_folder.mkdir(parents=True, exist_ok=True)
    
    # Create subdirectories for this session's logs
    log_c_dir = session_folder / "C"
    log_rust_dir = session_folder / "Rust"
    log_c_dir.mkdir(parents=True, exist_ok=True)
    log_rust_dir.mkdir(parents=True, exist_ok=True)

    all_c_lat: list[float] = []
    all_r_lat: list[float] = []
    looks: list[dict] = []
    final_decision = "CONTINUE"

    print("=" * 70)
    print("Sequential Validation Controller")
    print(f"Started     : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Session folder: {session_folder}")
    print(f"Block       : runs={args.block_runs}, samples={args.samples}")
    print(f"Stopping    : min={args.min_samples}, max={args.max_samples}, MDE={args.mde_pct:.3f}%")
    print(f"Confidence  : {args.confidence:.3f}")
    print(
        f"Consistency : min_looks={args.min_looks}, window={args.stability_window}, "
        f"max_cv_change={args.max_cv_change_pct:.4f}pp"
    )
    print(f"Order       : {'fixed C->RUST' if args.fixed_order else 'alternating by look'}")
    print("=" * 70)

    look_idx = 0
    while True:
        look_idx += 1
        before_c = list_logs(log_c_dir)
        before_r = list_logs(log_rust_dir)

        rc, order_label = run_block(args, look_idx, session_folder)
        if rc != 0:
            print(f"[controller] Block failed with exit code {rc}; stopping.")
            final_decision = "BLOCK_FAILED"
            break

        after_c = list_logs(log_c_dir)
        after_r = list_logs(log_rust_dir)
        new_c = sorted(after_c - before_c)
        new_r = sorted(after_r - before_r)

        block_c_lat: list[float] = []
        block_r_lat: list[float] = []

        for name in new_c:
            block_c_lat.extend(parse_latency_ms(log_c_dir / name))
        for name in new_r:
            block_r_lat.extend(parse_latency_ms(log_rust_dir / name))

        all_c_lat.extend(block_c_lat)
        all_r_lat.extend(block_r_lat)

        n_c = len(all_c_lat)
        n_r = len(all_r_lat)
        m_c, s_c = mean_std(all_c_lat)
        m_r, s_r = mean_std(all_r_lat)
        d, d_low, d_high = diff_ci(all_c_lat, all_r_lat, args.confidence)

        pooled_mean = (m_c + m_r) / 2.0 if (m_c + m_r) > 0 else 1.0
        rel = (d / pooled_mean) * 100.0
        rel_low = (d_low / pooled_mean) * 100.0
        rel_high = (d_high / pooled_mean) * 100.0

        look_rec = {
            "look": look_idx,
            "run_order": order_label,
            "new_c_logs": len(new_c),
            "new_r_logs": len(new_r),
            "new_c_latencies": len(block_c_lat),
            "new_r_latencies": len(block_r_lat),
            "total_c_latencies": n_c,
            "total_r_latencies": n_r,
            "mean_c_ms": m_c,
            "std_c_ms": s_c,
            "cv_c_pct": cv_pct(m_c, s_c),
            "mean_rust_ms": m_r,
            "std_rust_ms": s_r,
            "cv_rust_pct": cv_pct(m_r, s_r),
            "diff_ms_c_minus_rust": d,
            "diff_ci_low_ms": d_low,
            "diff_ci_high_ms": d_high,
            "rel_diff_pct": rel,
            "rel_ci_low_pct": rel_low,
            "rel_ci_high_pct": rel_high,
        }
        looks.append(look_rec)

        print("\n" + "-" * 70)
        print(f"Look {look_idx}")
        print(f"Order       : {order_label}")
        print(f"New logs    : C={len(new_c)} Rust={len(new_r)}")
        print(f"New latency : C={len(block_c_lat)} Rust={len(block_r_lat)}")
        print(f"Total N     : C={n_c} Rust={n_r}")
        print(f"Mean (ms)   : C={m_c:.4f} Rust={m_r:.4f}")
        print(f"CV (%)      : C={look_rec['cv_c_pct']:.4f} Rust={look_rec['cv_rust_pct']:.4f}")
        print(f"Diff C-R    : {d:.4f} ms [{d_low:.4f}, {d_high:.4f}]")
        print(f"Rel diff    : {rel:.4f}% [{rel_low:.4f}%, {rel_high:.4f}%]")

        min_n = min(n_c, n_r)

        # Check hard cap FIRST (before any continue statements can skip it)
        if min_n >= args.max_samples:
            print(f"Decision chk: MAX_CAP_REACHED (hit hard limit of {args.max_samples})")
            final_decision = "MAX_CAP_REACHED"
            break

        if min_n >= args.min_samples:
            if look_idx < args.min_looks:
                print(f"Decision chk: skipped (need at least {args.min_looks} looks, have {look_idx})")
                continue

            stable_ok, stable_msg = is_stable(looks, args.stability_window, args.max_cv_change_pct)
            print(f"Stability   : {stable_msg}")
            if not stable_ok:
                print("Decision chk: CONTINUE (consistency gate not met)")
                continue

            decision = classify(rel_low, rel_high, args.mde_pct)
            print(f"Decision chk: {decision}")
            if decision != "CONTINUE":
                final_decision = decision
                break
        else:
            print(f"Decision chk: skipped (need at least {args.min_samples}, have {min_n})")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    label = f"_{args.label}" if args.label else ""
    out_json = session_folder / f"sequential_validation{label}_{ts}.json"
    out_txt = session_folder / f"sequential_validation{label}_{ts}.txt"

    payload = {
        "started_at": ts,
        "params": {
            "samples": args.samples,
            "block_runs": args.block_runs,
            "min_samples": args.min_samples,
            "max_samples": args.max_samples,
            "mde_pct": args.mde_pct,
            "confidence": args.confidence,
            "warmup": args.warmup,
            "timeout": args.timeout,
            "min_looks": args.min_looks,
            "stability_window": args.stability_window,
            "max_cv_change_pct": args.max_cv_change_pct,
            "fixed_order": args.fixed_order,
        },
        "final_decision": final_decision,
        "total_c_latencies": len(all_c_lat),
        "total_rust_latencies": len(all_r_lat),
        "looks": looks,
    }

    with out_json.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    with out_txt.open("w", encoding="utf-8") as f:
        f.write("Sequential validation summary\n")
        f.write("=" * 60 + "\n")
        f.write(f"Decision: {final_decision}\n")
        f.write(f"min_looks: {args.min_looks}\n")
        f.write(f"stability_window: {args.stability_window}\n")
        f.write(f"max_cv_change_pct: {args.max_cv_change_pct}\n")
        f.write(f"fixed_order: {args.fixed_order}\n")
        f.write(f"Total C latency points   : {len(all_c_lat)}\n")
        f.write(f"Total Rust latency points: {len(all_r_lat)}\n")
        if looks:
            last = looks[-1]
            f.write(f"Mean C (ms): {last['mean_c_ms']:.6f}\n")
            f.write(f"Mean Rust (ms): {last['mean_rust_ms']:.6f}\n")
            f.write(
                "Rel diff (C-R) % CI: "
                f"{last['rel_diff_pct']:.6f}% "
                f"[{last['rel_ci_low_pct']:.6f}%, {last['rel_ci_high_pct']:.6f}%]\n"
            )

    print("\n" + "=" * 70)
    print(f"Final decision : {final_decision}")
    print(f"Saved JSON     : {out_json}")
    print(f"Saved summary  : {out_txt}")
    print("=" * 70)

    if final_decision == "BLOCK_FAILED":
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
