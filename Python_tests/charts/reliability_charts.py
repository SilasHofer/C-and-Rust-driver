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

COLORS = {"C": "#1f77b4", "Rust": "#ff7f0e", "Reference": "#333333"}
SUMMARY_RAM_PATTERN = re.compile(r'RAM\s+mean=([\d.]+)MB\s+min=([\d.]+)MB\s+max=([\d.]+)MB')

def add_stats_box_bottom_right(ax, stats_dict):
    """Formal summary box anchored in the bottom right corner."""
    order = ["Reference", "C", "Rust"]
    present = [l for l in order if l in stats_dict]

    text_lines = []
    for i, label in enumerate(present):
        if i > 0:
            text_lines.append("")  # blank separator between groups
        s = stats_dict[label]
        text_lines.append(f"$\\mathbf{{{label}}}$")
        text_lines.append(f" Mean: {s['mean']:.3f} \u00b0C")
        text_lines.append(f" Std Dev: {s['std']:.3f} \u00b0C")
        text_lines.append(f" n: {s['n']:,}")

    final_text = "\n".join(text_lines)
    ax.text(0.97, 0.05, final_text, transform=ax.transAxes, fontsize=9,
            verticalalignment='bottom', horizontalalignment='right',
            bbox=dict(boxstyle="round,pad=0.3", alpha=0.9, facecolor='white', edgecolor='#cccccc'))

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
print("  Generating Plot 1/5...")
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
add_stats_box_bottom_right(ax, stats)
plt.savefig(os.path.join(OUT_DIR, "stability_analysis.png"), bbox_inches='tight')

# 2. Boxplot (Bottom Right Box)
print("  Generating Plot 2/5...")
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

add_stats_box_bottom_right(ax, stats)
ax.set_title("Distribution of Sensor Readings")
ax.set_ylabel("Temperature ($^\circ$C)")
ax.grid(True, axis='y', linestyle='--', alpha=0.3)
plt.savefig(os.path.join(OUT_DIR, "distribution_boxplot.png"), bbox_inches='tight')

# 3. Accuracy (MAE)
print("  Generating Plot 3/5...")
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
print("  Generating Plot 4/5...")
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
print("  Generating Plot 5/5...")
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

print(f"\nSuccess. Thesis-ready charts saved to: {OUT_DIR}")