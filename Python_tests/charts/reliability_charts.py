import re
import sys
import os
import glob
import time
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime
from collections import defaultdict

# Set global plotting style for Thesis (High DPI, Formal Fonts)
plt.rcParams.update({
    'font.size': 12,
    'axes.titlesize': 14,
    'axes.labelsize': 12,
    'xtick.labelsize': 10,
    'ytick.labelsize': 10,
    'legend.fontsize': 10,
    'figure.dpi': 300
})

# ----------------------------
# Configuration
# ----------------------------
if len(sys.argv) < 2:
    print("Usage: python3 reliability_thesis.py <data_directory>")
    sys.exit(1)

DATA_DIR = sys.argv[1]
OUT_DIR = f"thesis_charts_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
os.makedirs(OUT_DIR, exist_ok=True)

COLORS = {"C": "#1f77b4", "Rust": "#ff7f0e", "Reference": "#333333", "Diff": "#2ca02c"}
SUMMARY_RAM_PATTERN = re.compile(r'RAM\s+mean=([\d.]+)MB\s+min=([\d.]+)MB\s+max=([\d.]+)MB')

def add_stats_box(ax, stats_dict, unit="\u00b0C", order=("Reference", "C", "Rust"),
                  fmt=".3f", position="bottom-right"):
    """Formal summary box anchored at a corner of the axes.

    position: 'bottom-right' (default, used by absolute-value plots) or 'top-right'
              (used by derivative plots so the box doesn't sit in the noise band).
    """
    present = [l for l in order if l in stats_dict]
    # Allow custom-keyed dicts (e.g. for difference plots) by appending leftovers.
    for k in stats_dict.keys():
        if k not in present:
            present.append(k)

    text_lines = []
    for i, label in enumerate(present):
        if i > 0:
            text_lines.append("")  # blank separator between groups
        s = stats_dict[label]
        # mathbf doesn't like spaces; collapse them for the bold header.
        bold_label = label.replace(" ", "\\,")
        text_lines.append(f"$\\mathbf{{{bold_label}}}$")
        text_lines.append(f" Mean: {s['mean']:{fmt}} {unit}")
        text_lines.append(f" Std Dev: {s['std']:{fmt}} {unit}")
        text_lines.append(f" n: {s['n']:,}")

    final_text = "\n".join(text_lines)

    if position == "top-right":
        anchor_x, anchor_y, va = 0.97, 0.95, 'top'
    else:  # bottom-right (default, preserves original behaviour)
        anchor_x, anchor_y, va = 0.97, 0.05, 'bottom'

    ax.text(anchor_x, anchor_y, final_text, transform=ax.transAxes, fontsize=9,
            verticalalignment=va, horizontalalignment='right',
            bbox=dict(boxstyle="round,pad=0.3", alpha=0.9, facecolor='white', edgecolor='#cccccc'))

def _sort_dedup(t, v):
    """Sort (t, v) by t and drop duplicate timestamps so gradient/interp behave."""
    t = np.asarray(t, dtype=float)
    v = np.asarray(v, dtype=float)
    if len(t) == 0:
        return t, v
    order = np.argsort(t)
    t, v = t[order], v[order]
    keep = np.concatenate(([True], np.diff(t) > 0))
    return t[keep], v[keep]

def _smooth_then_gradient(t, v, window_seconds=30.0):
    """Time-windowed rolling mean on v, then central-difference derivative.

    Returns dT/dt in deg C / hr.

    The window length is converted from seconds to samples using the median
    sample interval, so the same 30-second window applies regardless of whether
    the series is sampled at ~44 Hz (drivers) or ~700 Hz (reference).

    Smoothing first is essential: np.gradient on raw samples is dominated by
    sample-to-sample LSB noise divided by tiny dt values, producing physically
    meaningless 1000+ deg C/hr spikes. Smoothing collapses that noise floor
    to the actual thermal-drift scale.
    """
    if len(t) < 3:
        return np.array([], dtype=float)
    dt_med = float(np.median(np.diff(t)))
    if dt_med <= 0:
        return np.array([], dtype=float)
    window_samples = max(3, int(round(window_seconds / dt_med)))
    v_smooth = pd.Series(v).rolling(window=window_samples, center=True,
                                    min_periods=1).mean().to_numpy()
    return np.gradient(v_smooth, t) * 3600.0  # deg C / hr

# ----------------------------
# Specialized Parsers
# ----------------------------
def parse_source_of_truth(file_path):
    print(f"  Processing Source of Truth...")
    try:
        chunks = pd.read_csv(file_path, skiprows=1, names=["timestamp", "temp"], 
                             dtype={"temp": "float32"}, engine="c", chunksize=1_000_000)
        df = pd.concat([chunk.dropna() for chunk in chunks])
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        t0 = df["timestamp"].iloc[0]
        rel_time = (df["timestamp"] - t0).dt.total_seconds().values
        return {"time": rel_time, "temps": df["temp"].values, "t0": t0}
    except Exception as e:
        print(f"    Error parsing Reference: {e}")
        return None

def parse_driver_data(file, global_t0):
    try:
        df = pd.read_csv(file, header=0, names=["timestamp", "temp"], engine="c")
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.dropna()
        rel_time = (df["timestamp"] - global_t0).dt.total_seconds().values
        
        base = os.path.basename(file).split('_')[0]
        log_files = glob.glob(os.path.join(os.path.dirname(file), f"{base}*.log"))
        log_files += glob.glob(os.path.join(os.path.dirname(file), "system*.log"))
        
        ram_vals, spikes, restarts = [], 0, 0
        for l in log_files:
            with open(l, "r") as f:
                content = f.read()
                ram_matches = SUMMARY_RAM_PATTERN.findall(content)
                ram_vals.extend([float(m[0]) for m in ram_matches])
                s_match = re.search(r'spikes=(\d+)', content)
                r_match = re.search(r'restarts=(\d+)', content)
                if s_match: spikes = max(spikes, int(s_match.group(1)))
                if r_match: restarts = max(restarts, int(r_match.group(1)))

        return {"time": rel_time, "temps": df["temp"].values, "ram": ram_vals, "spikes": spikes, "restarts": restarts}
    except: return None

# ----------------------------
# Data Loading & Processing
# ----------------------------
ref_path = os.path.join(DATA_DIR, "temperature_readings.csv")
ref_data = parse_source_of_truth(ref_path) if os.path.exists(ref_path) else None
global_t0 = ref_data["t0"] if ref_data else datetime.now()

drivers = {"C": [], "Rust": []}
for f in glob.glob(os.path.join(DATA_DIR, "*.csv")):
    if "temperature_readings" in f: continue
    lbl = "C" if os.path.basename(f).lower().startswith("c_") else "Rust" if os.path.basename(f).lower().startswith("rust_") else None
    if lbl:
        data = parse_driver_data(f, global_t0)
        if data: drivers[lbl].append(data)

# Calculate Stats for the box
stats = {}
if ref_data:
    stats["Reference"] = {"mean": np.mean(ref_data["temps"]), "std": np.std(ref_data["temps"]), "n": len(ref_data["temps"])}
for lbl in ["C", "Rust"]:
    if drivers[lbl]:
        d_temps = np.concatenate([d["temps"] for d in drivers[lbl]])
        stats[lbl] = {"mean": np.mean(d_temps), "std": np.std(d_temps), "n": len(d_temps)}

# ----------------------------
# Charting Functions
# ----------------------------

# 1. Stability Line Chart (Hours + Bottom Right Box)
print("  Generating Plot 1/7...")
fig, ax = plt.subplots(figsize=(10, 5))
ax.set_ylim(18, 25.5)

if ref_data:
    ax.plot(ref_data["time"][::500] / 3600, ref_data["temps"][::500], 
            label="Ground Truth", color=COLORS["Reference"], alpha=0.25, linewidth=0.8)

for lbl in ["C", "Rust"]:
    if drivers[lbl]:
        all_t = np.concatenate([d["time"] for d in drivers[lbl]])
        all_v = np.concatenate([d["temps"] for d in drivers[lbl]])
        step = max(1, len(all_v) // 10000)
        ax.plot(all_t[::step] / 3600, all_v[::step], label=f"{lbl} Implementation", color=COLORS[lbl], alpha=0.8, linewidth=1.2)

ax.set_title("Long-term Temperature Stability Comparison")
ax.set_xlabel("Elapsed Time (hours)")
ax.set_ylabel("Temperature ($^\circ$C)")
ax.legend(loc='upper left', frameon=True)   
ax.grid(True, linestyle=':', alpha=0.6)
add_stats_box(ax, stats)
plt.savefig(os.path.join(OUT_DIR, "stability_analysis.png"), bbox_inches='tight')

# 2. Boxplot (Bottom Right Box)
print("  Generating Plot 2/7...")
fig, ax = plt.subplots(figsize=(8, 6))
ax.margins(y=0.3)
plot_data, labels = [], []
if ref_data:
    plot_data.append(ref_data["temps"]); labels.append("Reference")
for lbl in ["C", "Rust"]:
    if drivers[lbl]:
        plot_data.append(np.concatenate([d["temps"] for d in drivers[lbl]]))
        labels.append(lbl)

if plot_data:
    bp = ax.boxplot(plot_data, labels=labels, patch_artist=True, showfliers=False, widths=0.6)
    for patch, l in zip(bp['boxes'], labels):
        patch.set_facecolor(COLORS.get(l, "grey")); patch.set_alpha(0.5)
        patch.set_edgecolor('#333333')

add_stats_box(ax, stats)
ax.set_title("Distribution of Sensor Readings")
ax.set_ylabel("Temperature ($^\circ$C)")
ax.grid(True, axis='y', linestyle='--', alpha=0.3)
plt.savefig(os.path.join(OUT_DIR, "distribution_boxplot.png"), bbox_inches='tight')

# 3. Accuracy (MAE)
print("  Generating Plot 3/7...")
if ref_data:
    fig, ax = plt.subplots(figsize=(6, 5))
    mae_res = {}
    for lbl in ["C", "Rust"]:
        if drivers[lbl]:
            errors = []
            for d in drivers[lbl]:
                truth_interp = np.interp(d["time"], ref_data["time"], ref_data["temps"])
                errors.append(np.abs(d["temps"] - truth_interp))
            mae_res[lbl] = np.mean(np.concatenate(errors))
    
    if mae_res:
        bars = ax.bar(mae_res.keys(), mae_res.values(), color=[COLORS[k] for k in mae_res.keys()], width=0.5, edgecolor='#333333')
        ax.set_title("Mean Absolute Error from Ground Truth")
        ax.set_ylabel("Average Deviation ($^\circ$C)")
        for bar in bars:
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height(), f'{bar.get_height():.5f}', ha='center', va='bottom', fontsize=10)
    plt.tight_layout()
    plt.savefig(os.path.join(OUT_DIR, "accuracy_mae.png"))

# 4. Memory Usage
print("  Generating Plot 4/7...")
fig, ax = plt.subplots(figsize=(10, 4))
all_ram_kb = {}
for lbl in ["C", "Rust"]:
    all_ram = []
    all_times = []
    for d in drivers[lbl]:
        all_ram.extend(d["ram"])
        if len(d["time"]) > 0:
            interval = d["time"][-1] / max(len(d["ram"]) - 1, 1)
            all_times.extend([i * interval / 3600 for i in range(len(d["ram"]))])
    if all_ram:
        ram_kb = [v * 1000 for v in all_ram]
        all_ram_kb[lbl] = ram_kb
        x_axis = all_times if len(all_times) == len(ram_kb) else [i / 3600 for i in range(len(ram_kb))]
        ax.plot(x_axis, ram_kb, label=lbl, color=COLORS[lbl], linewidth=1.5, marker='o', markersize=3)

if all_ram_kb:
    all_vals = [v for vals in all_ram_kb.values() for v in vals]
    margin = (max(all_vals) - min(all_vals)) * 0.5 or 100
    ax.set_ylim(min(all_vals) - margin, max(all_vals) + margin)

ax.set_title("Memory over time")
ax.set_xlabel("Elapsed Time (hours)")
ax.set_ylabel("Memory (kB)")
ax.legend()
ax.grid(True, linestyle=':', alpha=0.6)
plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, "memory_profile.png"))

# 5. Reliability Events
print("  Generating Plot 5/7...")
fig, ax = plt.subplots(figsize=(7, 5))
x = np.arange(2)
spikes = [sum(d["spikes"] for d in drivers["C"]), sum(d["spikes"] for d in drivers["Rust"])]
restarts = [sum(d["restarts"] for d in drivers["C"]), sum(d["restarts"] for d in drivers["Rust"])]
ax.bar(x-0.15, spikes, 0.3, label='Sensor Spikes', color='#e74c3c', alpha=0.7, edgecolor='black')
ax.bar(x+0.15, restarts, 0.3, label='System Restarts', color='#c0392b', edgecolor='black')
ax.set_xticks(x); ax.set_xticklabels(["C", "Rust"])
ax.set_ylim(bottom=0)
ax.set_ylabel("Event Count")
ax.set_title("System Reliability Events")
ax.legend()
plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, "reliability_events.png"))

# 6. Derivative Stability — smoothed before differentiating
# Differentiation removes any constant calibration offset between sensors, so the
# three lines should overlap if they're tracking the same physical signal.
# A 30-second rolling mean is applied to the temperature series BEFORE taking
# the gradient. Without smoothing, np.gradient on raw samples is dominated by
# sample-spacing x LSB-noise artifacts (~1000+ deg C/hr) and the visual is unusable.
print("  Generating Plot 6/7...")
fig, ax = plt.subplots(figsize=(10, 5))

SMOOTH_WINDOW_SECONDS = 30.0
DERIV_CLIP_SECONDS = 60.0   # exclude startup transient (consistent with Plot 7)
deriv_stats = {}

if ref_data:
    ref_t, ref_v = _sort_dedup(ref_data["time"], ref_data["temps"])
    if len(ref_t) > 2:
        ref_deriv = _smooth_then_gradient(ref_t, ref_v, SMOOTH_WINDOW_SECONDS)
        if len(ref_deriv) > 0:
            # Clip first N seconds for both stats and plotting
            clip_mask = ref_t >= DERIV_CLIP_SECONDS
            ref_t_clipped = ref_t[clip_mask]
            ref_deriv_clipped = ref_deriv[clip_mask]
            if len(ref_deriv_clipped) > 0:
                deriv_stats["Reference"] = {
                    "mean": float(np.mean(ref_deriv_clipped)),
                    "std": float(np.std(ref_deriv_clipped)),
                    "n": int(len(ref_deriv_clipped)),
                }
                plot_step = max(1, len(ref_deriv_clipped) // 50000)
                ax.plot(ref_t_clipped[::plot_step] / 3600, ref_deriv_clipped[::plot_step],
                        label="Ground Truth",
                        color=COLORS["Reference"], alpha=0.5, linewidth=0.8)

for lbl in ["C", "Rust"]:
    if not drivers[lbl]:
        continue
    all_t = np.concatenate([d["time"] for d in drivers[lbl]])
    all_v = np.concatenate([d["temps"] for d in drivers[lbl]])
    all_t, all_v = _sort_dedup(all_t, all_v)
    if len(all_t) < 3:
        continue

    deriv = _smooth_then_gradient(all_t, all_v, SMOOTH_WINDOW_SECONDS)
    if len(deriv) == 0:
        continue

    # Clip first N seconds for both stats and plotting
    clip_mask = all_t >= DERIV_CLIP_SECONDS
    t_clipped = all_t[clip_mask]
    deriv_clipped = deriv[clip_mask]
    if len(deriv_clipped) == 0:
        continue

    deriv_stats[lbl] = {
        "mean": float(np.mean(deriv_clipped)),
        "std": float(np.std(deriv_clipped)),
        "n": int(len(deriv_clipped)),
    }
    plot_step = max(1, len(deriv_clipped) // 10000)
    ax.plot(t_clipped[::plot_step] / 3600, deriv_clipped[::plot_step],
            label=f"{lbl} Implementation",
            color=COLORS[lbl], alpha=0.8, linewidth=1.2)

ax.axhline(0, color='black', linewidth=0.5, linestyle='--', alpha=0.5)
ax.set_title(f"Long-term Temperature Derivative Stability "
             f"({int(SMOOTH_WINDOW_SECONDS)}s smoothed)")
ax.set_xlabel("Elapsed Time (hours)")
ax.set_ylabel("dT/dt ($^\\circ$C/hr)")
ax.legend(loc='upper left', frameon=True)
ax.grid(True, linestyle=':', alpha=0.6)

# Transparency note about the startup clip
ax.text(0.01, 0.02,
        f"first {DERIV_CLIP_SECONDS:.0f}\u202fs of run excluded",
        transform=ax.transAxes, fontsize=8, color='#666666',
        verticalalignment='bottom', horizontalalignment='left')

add_stats_box(ax, deriv_stats, unit="\u00b0C/hr", fmt=".3f", position="top-right")
plt.savefig(os.path.join(OUT_DIR, "derivative_stability.png"), bbox_inches='tight')

# 7. Pairwise Derivative Differences (C-Ref, Rust-Ref, C-Rust)
# All series interpolated onto a common time grid, differentiated, then subtracted.
# Strips out per-sensor baseline so any deviation from zero is a *real* tracking difference.
#
# n_points = 20,000 -- enough sample size for credible mean/std without drowning the
# C-Rust signal in interpolation noise.
# Startup transient (first STARTUP_CLIP_SECONDS) excluded -- np.gradient at the
# linspace boundary plus any sensor warm-up makes the t=0 value an outlier that
# just stretches the y-axis.
print("  Generating Plot 7/7...")

STARTUP_CLIP_SECONDS = 60.0

c_t = c_v = r_t = r_v = None
if drivers["C"]:
    c_t, c_v = _sort_dedup(np.concatenate([d["time"] for d in drivers["C"]]),
                           np.concatenate([d["temps"] for d in drivers["C"]]))
if drivers["Rust"]:
    r_t, r_v = _sort_dedup(np.concatenate([d["time"] for d in drivers["Rust"]]),
                           np.concatenate([d["temps"] for d in drivers["Rust"]]))

have_ref = ref_data is not None and len(ref_data["time"]) > 1
have_c = c_t is not None and len(c_t) > 1
have_r = r_t is not None and len(r_t) > 1

# We need at least one of: (ref + driver) or (C + Rust) to draw any difference line.
can_plot = (have_ref and (have_c or have_r)) or (have_c and have_r)

if can_plot:
    fig, ax = plt.subplots(figsize=(10, 5))

    # Build common time grid over the overlap of available series
    t_min = -np.inf
    t_max = np.inf
    if have_ref:
        ref_t_sorted, ref_v_sorted = _sort_dedup(ref_data["time"], ref_data["temps"])
        t_min = max(t_min, ref_t_sorted[0])
        t_max = min(t_max, ref_t_sorted[-1])
    if have_c:
        t_min = max(t_min, c_t[0])
        t_max = min(t_max, c_t[-1])
    if have_r:
        t_min = max(t_min, r_t[0])
        t_max = min(t_max, r_t[-1])

    # Skip the first STARTUP_CLIP_SECONDS so the boundary spike at t=0 doesn't
    # pin the y-axis open.
    t_min_eff = t_min + STARTUP_CLIP_SECONDS

    if t_max > t_min_eff:
        n_points = 20_000
        common_t = np.linspace(t_min_eff, t_max, n_points)

        ref_deriv = c_deriv = r_deriv = None
        if have_ref:
            ref_at = np.interp(common_t, ref_t_sorted, ref_v_sorted)
            ref_deriv = np.gradient(ref_at, common_t) * 3600.0
        if have_c:
            c_at = np.interp(common_t, c_t, c_v)
            c_deriv = np.gradient(c_at, common_t) * 3600.0
        if have_r:
            r_at = np.interp(common_t, r_t, r_v)
            r_deriv = np.gradient(r_at, common_t) * 3600.0

        # Decimate plotted line to ~10k points; stats use the full 20k arrays.
        plot_step = max(1, n_points // 10000)
        x_hours_plot = common_t[::plot_step] / 3600
        diff_stats = {}

        if have_c and have_ref:
            arr = c_deriv - ref_deriv
            ax.plot(x_hours_plot, arr[::plot_step], label="C \u2212 Reference",
                    color=COLORS["C"], alpha=0.75, linewidth=1.0)
            diff_stats["C-Ref"] = {
                "mean": float(np.mean(arr)),
                "std": float(np.std(arr)),
                "n": int(len(arr)),
            }
        if have_r and have_ref:
            arr = r_deriv - ref_deriv
            ax.plot(x_hours_plot, arr[::plot_step], label="Rust \u2212 Reference",
                    color=COLORS["Rust"], alpha=0.75, linewidth=1.0)
            diff_stats["Rust-Ref"] = {
                "mean": float(np.mean(arr)),
                "std": float(np.std(arr)),
                "n": int(len(arr)),
            }
        if have_c and have_r:
            arr = c_deriv - r_deriv
            ax.plot(x_hours_plot, arr[::plot_step], label="C \u2212 Rust",
                    color=COLORS["Diff"], alpha=0.85, linewidth=1.0)
            diff_stats["C-Rust"] = {
                "mean": float(np.mean(arr)),
                "std": float(np.std(arr)),
                "n": int(len(arr)),
            }

        ax.axhline(0, color='black', linewidth=0.5, linestyle='--', alpha=0.5)
        ax.set_title("Pairwise Derivative Differences (Baseline-Independent Comparison)")
        ax.set_xlabel("Elapsed Time (hours)")
        ax.set_ylabel("$\\Delta$(dT/dt) ($^\\circ$C/hr)")
        ax.legend(loc='upper left', frameon=True)
        ax.grid(True, linestyle=':', alpha=0.6)

        # Transparency note about the startup clip (shown in the chart, mention
        # in the thesis caption too).
        ax.text(0.01, 0.02,
                f"first {STARTUP_CLIP_SECONDS:.0f}\u202fs of run excluded",
                transform=ax.transAxes, fontsize=8, color='#666666',
                verticalalignment='bottom', horizontalalignment='left')

        if diff_stats:
            add_stats_box(
                ax, diff_stats,
                unit="\u00b0C/hr",
                order=("C-Ref", "Rust-Ref", "C-Rust"),
                fmt=".4f",
                position="top-right",
            )

        plt.savefig(os.path.join(OUT_DIR, "derivative_differences.png"), bbox_inches='tight')

print(f"\nSuccess. Thesis-ready charts saved to: {OUT_DIR}")