import re
import sys
import os
import glob
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
from collections import defaultdict

# ----------------------------
# Input
# ----------------------------
if len(sys.argv) < 2:
    print("Usage: python3 charts.py <log_list_file>")
    sys.exit(1)

LOG_LIST_FILE = sys.argv[1]

# Read log/folder paths
with open(LOG_LIST_FILE, "r") as f:
    INPUT_PATHS = [line.strip() for line in f if line.strip() and not line.startswith("#")]

OUT_DIR = f"comparison_plots_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
os.makedirs(OUT_DIR, exist_ok=True)
print(f"Output folder: {OUT_DIR}")

# ----------------------------
# Log parsing (now also extracts CPU mean and peak from summary)
# ----------------------------
pattern = re.compile(
    r'\[(\d+/\d+)\]\s+\[(.*?)\]\s+Temperature:\s+([\d.]+)\s+C\s+\|\s+mem=([\d.]+)KB\s+(?:cpu|cpu_delta)=([\d.]+)s\s+lat=([\d.]+|N/A)ms'
)

def parse_log(file):
    timestamps, lat, mem, cpu = [], [], [], []
    cpu_mean_pct = None
    cpu_peak_pct = None

    with open(file, "r") as f:
        for line in f:
            # Per-line data
            m = pattern.search(line)
            if m:
                try:
                    ts = datetime.strptime(m.group(2), "%Y-%m-%d %H:%M:%S.%f")
                except:
                    continue
                timestamps.append(ts)
                mem.append(float(m.group(4)))
                cpu.append(float(m.group(5)))
                lat_val = m.group(6)
                if lat_val != "N/A":
                    lat.append(float(lat_val))

            # Final summary CPU values
            if "CPU mean :" in line:
                try:
                    cpu_mean_pct = float(line.split("CPU mean :")[1].split("%")[0].strip())
                except:
                    pass
            if "CPU peak :" in line:
                try:
                    cpu_peak_pct = float(line.split("CPU peak :")[1].split("%")[0].strip())
                except:
                    pass

    if not timestamps:
        return None

    duration = (timestamps[-1] - timestamps[0]).total_seconds()
    throughput = len(timestamps) / duration if duration > 0 else 0

    return {
        "file": os.path.basename(file),
        "timestamps": np.array(timestamps),
        "lat": np.array(lat),
        "mem": np.array(mem),
        "cpu": np.array(cpu),
        "throughput": throughput,
        "samples": len(timestamps),
        "cpu_mean_pct": cpu_mean_pct,
        "cpu_peak_pct": cpu_peak_pct
    }

def load_input(path):
    files = []
    if os.path.isdir(path):
        files = glob.glob(os.path.join(path, "*.log"))
    elif os.path.isfile(path):
        files = [path]
    else:
        print(f"Warning: {path} not found")
        return []
    data_list = []
    for f in files:
        d = parse_log(f)
        if d:
            data_list.append(d)
    return data_list

# ----------------------------
# Load all logs
# ----------------------------
all_data = {}
for path in INPUT_PATHS:
    all_data[path] = load_input(path)

# ----------------------------
# Group logs by sample size per driver
# ----------------------------
grouped_data = {}
for driver, logs in all_data.items():
    grouped_data[driver] = defaultdict(list)
    for d in logs:
        grouped_data[driver][d["samples"]].append(d)

# ----------------------------
# Plotting helpers
# ----------------------------
def moving_avg(data, window):
    out = []
    for i in range(len(data)):
        vals = [v for v in data[max(0,i-window):i+window+1] if v is not None]
        out.append(sum(vals)/len(vals) if vals else None)
    return out

def add_stats_text(ax, stats_dict: dict):
    """Add clean stats box in top-right corner"""
    text = ""
    for label, s in stats_dict.items():
        text += f"{label}:\n"
        text += f"  Mean: {s['mean']:.3f} ms\n"
        text += f"  Std:  {s['std']:.3f} ms\n"
        text += f"  95th: {s['p95']:.3f} ms\n"
        text += f"  Min:  {s['min']:.3f} ms\n"
        text += f"  Max:  {s['max']:.3f} ms\n"
        text += f"  N:    {s['n']:,}\n\n"
    ax.text(0.98, 0.98, text.strip(), transform=ax.transAxes, fontsize=8,
            horizontalalignment='right', verticalalignment='top',
            bbox=dict(boxstyle="round,pad=0.3", alpha=0.07, facecolor='white'))

# ----------------------------
# Individual analysis per driver (ONLY latency now)
# ----------------------------
for driver, samples_dict in grouped_data.items():
    driver_name = os.path.basename(os.path.normpath(driver))
    driver_name = driver_name.replace("Performance", "").replace("Logs", "").strip("_ /")

    for sample_size, logs in samples_dict.items():
        min_len = min([len(d["lat"]) for d in logs])
        avg_lat = np.mean([d["lat"][:min_len] for d in logs], axis=0)
        avg_timestamps = logs[0]["timestamps"][:min_len]
        seconds = [(ts - avg_timestamps[0]).total_seconds() for ts in avg_timestamps]

        lat_stats = {
            "mean": float(np.mean(avg_lat)),
            "std": float(np.std(avg_lat)),
            "p95": float(np.percentile(avg_lat, 95)),
            "min": float(np.min(avg_lat)),
            "max": float(np.max(avg_lat)),
            "n": int(len(avg_lat))
        }

        # Latency over time
        window = max(1, len(avg_lat)//50)
        trend = moving_avg(avg_lat, window)
        fig, ax = plt.subplots(figsize=(12,5))
        ax.plot(seconds, avg_lat, label="Latency", alpha=0.7)
        ax.plot(seconds, trend, label=f"Trend ±{window}", linewidth=2)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Latency (ms)")
        ax.set_title(f"{driver_name} - Latency over time ({sample_size} readings)")
        ax.legend()
        add_stats_text(ax, {driver_name: lat_stats})
        plt.tight_layout()
        plt.savefig(os.path.join(OUT_DIR, f"{driver_name}_latency_{sample_size}.png"), dpi=150)
        plt.close()

# ----------------------------
# Comparison plots
# ----------------------------
driver_names = list(all_data.keys())
latencies = []
memories = []
cpu_means = []
cpu_peaks = []

clean_names = []
for d in driver_names:
    name = os.path.basename(os.path.normpath(d))
    name = name.replace("Performance", "").replace("Logs", "").strip("_ /")
    clean_names.append(name)

for driver in driver_names:
    driver_lat = []
    driver_mem = []
    driver_cpu_mean = []
    driver_cpu_peak = []
    for sample_size, logs in grouped_data[driver].items():
        min_len = min([len(d["lat"]) for d in logs])
        driver_lat.append(np.mean([d["lat"][:min_len] for d in logs], axis=0))
        driver_mem.append(np.mean([d["mem"][:min_len] for d in logs], axis=0))
        
        # Collect CPU stats (skip None values)
        for d in logs:
            if d.get("cpu_mean_pct") is not None:
                driver_cpu_mean.append(d["cpu_mean_pct"])
            if d.get("cpu_peak_pct") is not None:
                driver_cpu_peak.append(d["cpu_peak_pct"])

    latencies.append(np.concatenate(driver_lat))
    memories.append(np.concatenate(driver_mem))
    cpu_means.append(np.mean(driver_cpu_mean) if driver_cpu_mean else 0)
    cpu_peaks.append(np.mean(driver_cpu_peak) if driver_cpu_peak else 0)

# Prepare latency stats for combined plots
combined_stats = {}
for name, lat in zip(clean_names, latencies):
    combined_stats[name] = {
        "mean": float(np.mean(lat)),
        "std": float(np.std(lat)),
        "p95": float(np.percentile(lat, 95)),
        "min": float(np.min(lat)),
        "max": float(np.max(lat)),
        "n": int(len(lat))
    }

# 1. Box plot latency
fig, ax = plt.subplots(figsize=(6,5))
ax.boxplot(latencies, labels=clean_names, showfliers=True)
ax.set_ylabel("Latency (ms)")
ax.set_title("Latency distribution (with outliers)")
add_stats_text(ax, combined_stats)
plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, "figure1_box_latency.png"), dpi=150)
plt.close()

# 2. CDF latency
fig, ax = plt.subplots(figsize=(10,5))
for name, lat in zip(clean_names, latencies):
    sorted_data = np.sort(lat)
    yvals = np.arange(len(sorted_data))/float(len(sorted_data))
    ax.plot(sorted_data, yvals, label=name)
ax.set_xlabel("Latency (ms)")
ax.set_ylabel("CDF")
ax.set_title("Latency CDF")
ax.grid(True, linestyle="--", alpha=0.4)
ax.legend()
add_stats_text(ax, combined_stats)
plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, "figure2_cdf_latency.png"), dpi=150)
plt.close()

# 3. Memory over time (combined)
fig, ax = plt.subplots(figsize=(10,5))
for name, mem in zip(clean_names, memories):
    ax.plot(mem, label=name)
ax.set_xlabel("Reading index")
ax.set_ylabel("Memory (KB)")
ax.set_title("Memory over time")
ax.legend()
plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, "figure3_memory.png"), dpi=150)
plt.close()

# 4. Histogram latency
fig, ax = plt.subplots(figsize=(10,5))
all_lat_combined = np.concatenate(latencies)
lower, upper = np.percentile(all_lat_combined, [1, 99])
latencies_clipped = [np.clip(lat, lower, upper) for lat in latencies]
bins = np.linspace(lower, upper, 50)
for name, lat in zip(clean_names, latencies_clipped):
    ax.hist(lat, bins=bins, alpha=0.6, label=name)
ax.set_xlabel("Latency (ms)")
ax.set_ylabel("Frequency")
ax.set_title("Latency Histogram: Direct Comparison (1-99 percentile)")
ax.legend()
add_stats_text(ax, combined_stats)
plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, "figure4_hist_latency.png"), dpi=150)
plt.close()

# NEW: CPU Mean & Peak comparison (grouped bar chart)
fig, ax = plt.subplots(figsize=(8,6))
x = np.arange(len(clean_names))
width = 0.35

ax.bar(x - width/2, cpu_means, width, label='CPU Mean %', alpha=0.85)
ax.bar(x + width/2, cpu_peaks, width, label='CPU Peak %', alpha=0.85)

ax.set_ylabel("CPU Usage (%)")
ax.set_title("CPU Usage Comparison (Mean and Peak)")
ax.set_xticks(x)
ax.set_xticklabels(clean_names)
ax.legend()

# Add value labels on bars
for i, v in enumerate(cpu_means):
    ax.text(i - width/2, v + 0.5, f"{v:.1f}", ha='center', fontsize=9)
for i, v in enumerate(cpu_peaks):
    ax.text(i + width/2, v + 0.5, f"{v:.1f}", ha='center', fontsize=9)

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, "figure5_cpu_usage.png"), dpi=150)
plt.close()

print(f"All plots saved in {OUT_DIR}")
print("   → New CPU comparison chart: figure5_cpu_usage.png")