#!/usr/bin/env python3
"""
Sequential validation chart generator.

Produces three figures from one or two sequential_validation JSON files:
  1. Convergence plot  — rel_diff_pct with CI band over looks, MDE thresholds
  2. Forest plot       — final CI per run as horizontal bars
  3. Summary table     — key parameters and outcome per run

Usage:
    python3 sequential_charts.py run1.json [run2.json] [--labels "99%/0.1%" "95%/1.0%"]

If two files are given they are shown side-by-side / overlaid for easy comparison.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DECISION_LABELS = {
    "C_FASTER":               "C faster",
    "RUST_FASTER":            "Rust faster",
    "PRACTICALLY_EQUIVALENT": "Practically equivalent",
    "MAX_CAP_REACHED":        "Max cap reached",
    "BLOCK_FAILED":           "Block failed",
    "CONTINUE":               "Did not converge",
}

COLORS = ["#2196F3", "#FF5722"]   # blue for run1, orange for run2
FILL_ALPHA = 0.15


def load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_series(data: dict) -> dict:
    """Pull per-look arrays from a JSON result."""
    looks      = [l["look"]            for l in data["looks"]]
    rel_diff   = [l["rel_diff_pct"]    for l in data["looks"]]
    ci_low     = [l["rel_ci_low_pct"]  for l in data["looks"]]
    ci_high    = [l["rel_ci_high_pct"] for l in data["looks"]]
    total_n    = [l["total_c_latencies"] for l in data["looks"]]
    return dict(looks=looks, rel_diff=rel_diff, ci_low=ci_low,
                ci_high=ci_high, total_n=total_n)


def final_look(data: dict) -> dict:
    return data["looks"][-1]


# ---------------------------------------------------------------------------
# Figure 1: Convergence plot
# ---------------------------------------------------------------------------

def plot_convergence(runs: list[dict], labels: list[str], out_path: str) -> None:
    n_runs = len(runs)
    fig, axes = plt.subplots(n_runs, 1, figsize=(7, 4 * n_runs), squeeze=False)

    for col, (data, label, color) in enumerate(zip(runs, labels, COLORS)):
        ax = axes[col][0]
        s = extract_series(data)
        mde = data["params"]["mde_pct"]
        conf = data["params"]["confidence"]
        decision = data["final_decision"]

        looks    = np.array(s["looks"])
        rel_diff = np.array(s["rel_diff"])
        ci_low   = np.array(s["ci_low"])
        ci_high  = np.array(s["ci_high"])

        # CI band
        ax.fill_between(looks, ci_low, ci_high, alpha=FILL_ALPHA, color=color, label="CI band")
        # CI boundary lines (thin, same colour)
        ax.plot(looks, ci_low,  color=color, linewidth=0.8, linestyle="--", alpha=0.6)
        ax.plot(looks, ci_high, color=color, linewidth=0.8, linestyle="--", alpha=0.6)
        # Point estimate
        ax.plot(looks, rel_diff, color=color, linewidth=2.0, label="Rel. diff (C − Rust) %")
        # Zero line
        ax.axhline(0, color="black", linewidth=0.8, linestyle=":")

        # MDE threshold lines
        ax.axhline( mde, color="red", linewidth=1.2, linestyle="--", alpha=0.7, label=f"±MDE ({mde}%)")
        ax.axhline(-mde, color="red", linewidth=1.2, linestyle="--", alpha=0.7)

        # MDE shaded zone
        y_lim_pad = max(abs(ci_low.min()), abs(ci_high.max())) * 1.25
        ax.set_ylim(-y_lim_pad, y_lim_pad)
        ax.axhspan(-mde, mde, color="red", alpha=0.04, label="Equiv. zone")

        # Stopping look marker
        stop_look = looks[-1]
        ax.axvline(stop_look, color="grey", linewidth=1.0, linestyle=":", alpha=0.8)
        ax.text(stop_look, ax.get_ylim()[1] * 0.92,
                f" stop\n look {stop_look}", fontsize=7.5, color="grey", va="top")

        decision_str = DECISION_LABELS.get(decision, decision)
        ax.set_title(f"{label}\n"
                     f"conf={int(conf*100)}%  MDE=±{mde}%  →  {decision_str}",
                     fontsize=10)
        ax.set_xlabel("Look (sequential block)")
        ax.set_ylabel("Relative difference C − Rust (%)")
        ax.legend(fontsize=8, loc="upper right")
        ax.grid(True, linestyle="--", alpha=0.3)

        # Secondary x-axis showing approx total N
        n_vals = s["total_n"]
        tick_idx   = list(range(0, len(looks), max(1, len(looks)//6)))
        tick_looks = [looks[i] for i in tick_idx]
        tick_n     = [n_vals[i] for i in tick_idx]
        ax2 = ax.twiny()
        ax2.set_xlim(ax.get_xlim())
        ax2.set_xticks(tick_looks)
        ax2.set_xticklabels([f"{n//1000}k" for n in tick_n], fontsize=7)
        ax2.set_xlabel("Approx. total N per driver", fontsize=8)

    fig.suptitle("Sequential validation — convergence over looks",
                 fontsize=12, fontweight="bold", y=1.02)
    plt.tight_layout(h_pad=3.0)
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {out_path}")


# ---------------------------------------------------------------------------
# Figure 2: Forest / CI comparison plot
# ---------------------------------------------------------------------------

def plot_forest(runs: list[dict], labels: list[str], out_path: str) -> None:
    fig, ax = plt.subplots(figsize=(8, 3 + len(runs) * 0.9))

    # Collect all MDE values to draw threshold bands
    all_mde = sorted(set(d["params"]["mde_pct"] for d in runs))

    # Draw MDE bands (widest first so narrower ones sit on top)
    band_alphas = [0.06, 0.10]
    for i, mde in enumerate(sorted(all_mde, reverse=True)):
        alpha = band_alphas[min(i, len(band_alphas)-1)]
        ax.axvspan(-mde, mde, color="red", alpha=alpha,
                   label=f"Equiv. zone ±{mde}%" if i == 0 else f"±{mde}%")

    ax.axvline(0, color="black", linewidth=0.9, linestyle=":")

    y_positions = list(range(len(runs) - 1, -1, -1))  # top = run 0

    # Compute x-limits first so annotation placement is stable
    all_vals = []
    for d in runs:
        last = final_look(d)
        all_vals += [last["rel_ci_low_pct"], last["rel_ci_high_pct"]]
    span = max(all_vals) - min(all_vals) or 0.5
    pad  = span * 0.45
    x_min = min(all_vals) - pad
    x_max = max(all_vals) + pad * 2.5   # extra right room for annotations
    ax.set_xlim(x_min, x_max)

    for y, (data, label, color) in zip(y_positions, zip(runs, labels, COLORS)):
        last = final_look(data)
        est  = last["rel_diff_pct"]
        low  = last["rel_ci_low_pct"]
        high = last["rel_ci_high_pct"]
        n    = last["total_c_latencies"]
        conf = data["params"]["confidence"]
        decision = DECISION_LABELS.get(data["final_decision"], data["final_decision"])

        ax.plot([low, high], [y, y], color=color, linewidth=2.5, solid_capstyle="round")
        ax.plot(est, y, marker="D", color=color, markersize=8, zorder=5)
        ax.text(high + span * 0.08,
                y,
                f"{est:+.3f}%  [{low:+.3f}, {high:+.3f}]\n"
                f"N={n:,}  conf={int(conf*100)}%  → {decision}",
                va="center", fontsize=8.5, color=color)

    ax.set_yticks(y_positions)
    ax.set_yticklabels(labels, fontsize=10)
    ax.set_xlabel("Relative difference C − Rust (%)", fontsize=10)
    ax.set_title("Final confidence intervals — forest plot", fontsize=11, fontweight="bold")
    ax.legend(fontsize=8, loc="lower left")
    ax.grid(True, axis="x", linestyle="--", alpha=0.3)

    plt.tight_layout()
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {out_path}")


# ---------------------------------------------------------------------------
# Figure 3: Summary table
# ---------------------------------------------------------------------------

def plot_table(runs: list[dict], labels: list[str], out_path: str) -> None:
    rows = []
    col_headers = [
        "Run", "N (per driver)", "Conf.", "MDE",
        "Mean C (ms)", "Mean Rust (ms)",
        "Abs. diff (ms)", "Rel. diff (%)",
        f"CI low (%)", "CI high (%)", "Decision"
    ]

    for data, label in zip(runs, labels):
        last  = final_look(data)
        p     = data["params"]
        decision = DECISION_LABELS.get(data["final_decision"], data["final_decision"])
        rows.append([
            label,
            f"{last['total_c_latencies']:,}",
            f"{int(p['confidence']*100)}%",
            f"±{p['mde_pct']}%",
            f"{last['mean_c_ms']:.4f}",
            f"{last['mean_rust_ms']:.4f}",
            f"{last['diff_ms_c_minus_rust']:+.4f}",
            f"{last['rel_diff_pct']:+.4f}%",
            f"{last['rel_ci_low_pct']:+.4f}%",
            f"{last['rel_ci_high_pct']:+.4f}%",
            decision,
        ])

    fig, ax = plt.subplots(figsize=(max(14, len(col_headers) * 1.3), 1.6 + len(runs) * 0.7))
    ax.axis("off")

    tbl = ax.table(
        cellText=rows,
        colLabels=col_headers,
        cellLoc="center",
        loc="center",
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)
    tbl.scale(1.0, 1.8)

    # Style header row
    for col in range(len(col_headers)):
        tbl[0, col].set_facecolor("#1565C0")
        tbl[0, col].set_text_props(color="white", fontweight="bold")

    # Alternate row shading
    for row in range(1, len(rows) + 1):
        fc = "#E3F2FD" if row % 2 == 0 else "white"
        for col in range(len(col_headers)):
            tbl[row, col].set_facecolor(fc)

    # Highlight decision column
    dec_col = len(col_headers) - 1
    decision_colors = {
        "C faster":               "#FFECB3",
        "Practically equivalent": "#C8E6C9",
        "Max cap reached":        "#FFE0B2",
    }
    for row_idx, row in enumerate(rows):
        dec = row[dec_col]
        color = decision_colors.get(dec, "#F5F5F5")
        tbl[row_idx + 1, dec_col].set_facecolor(color)

    ax.set_title("Sequential validation — run summary", fontsize=12,
                 fontweight="bold", pad=12)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {out_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate sequential validation charts from JSON result files.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("json_files", nargs="+", help="One or two sequential_validation JSON files")
    p.add_argument(
        "--labels", nargs="+",
        help="Short label per file, e.g. '99%% / 0.1%%' '95%% / 1.0%%'"
    )
    p.add_argument("--out-dir", default="", help="Output directory (default: next to first JSON)")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    if len(args.json_files) > 2:
        print("ERROR: at most two JSON files supported")
        sys.exit(1)

    runs = [load(p) for p in args.json_files]

    # Auto-labels from params if not given
    if args.labels and len(args.labels) == len(runs):
        labels = args.labels
    else:
        labels = []
        for d in runs:
            p = d["params"]
            labels.append(f"conf={int(p['confidence']*100)}% / MDE=±{p['mde_pct']}%")

    # Output directory
    if args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        out_dir = Path(args.json_files[0]).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"\nGenerating sequential analysis charts → {out_dir}\n")

    plot_convergence(runs, labels, str(out_dir / f"seq_convergence_{ts}.png"))
    plot_forest     (runs, labels, str(out_dir / f"seq_forest_{ts}.png"))
    plot_table      (runs, labels, str(out_dir / f"seq_table_{ts}.png"))

    print("\nDone.")


if __name__ == "__main__":
    main()