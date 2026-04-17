import pandas as pd
import matplotlib.pyplot as plt
import os

# -----------------------------
# CREATE OUTPUT FOLDER
# -----------------------------
output_dir = "c"
os.makedirs(output_dir, exist_ok=True)

# -----------------------------
# LOAD CSV SAFELY
# -----------------------------
df = pd.read_csv(
    "c_complexity.csv",
    engine="python",
    on_bad_lines="skip"
)

df = df.dropna(subset=["Function", "CyclomaticComplexity"])

# ensure correct types
df["Function"] = df["Function"].astype(str)
df["CyclomaticComplexity"] = pd.to_numeric(df["CyclomaticComplexity"], errors="coerce")

# -----------------------------
# REMOVE ONLY main + internal symbols
# -----------------------------
df = df[~df["Function"].str.startswith("_")]
df = df[df["Function"] != "main"]

# -----------------------------
# SORT
# -----------------------------
df = df.sort_values(by="CyclomaticComplexity", ascending=False)

# -----------------------------
# BAR CHART WITH LABELS
# -----------------------------
plt.figure(figsize=(12, 6))

bars = plt.bar(df["Function"], df["CyclomaticComplexity"])

plt.xticks(rotation=90)
plt.ylabel("Cyclomatic Complexity")
plt.title("Rust Driver Complexity")

# add value labels on top of bars
for bar in bars:
    height = bar.get_height()
    plt.text(
        bar.get_x() + bar.get_width() / 2,
        height,
        f"{int(height)}",
        ha="center",
        va="bottom",
        fontsize=9
    )

plt.tight_layout()

bar_path = os.path.join(output_dir, "complexity_filtered.png")
plt.savefig(bar_path, dpi=300, bbox_inches="tight")
print(f"Saved: {bar_path}")

# -----------------------------
# HISTOGRAM
# -----------------------------
plt.figure()

plt.hist(df["CyclomaticComplexity"], bins=8)

plt.xlabel("Cyclomatic Complexity")
plt.ylabel("Number of Functions")
plt.title("Complexity Distribution")

hist_path = os.path.join(output_dir, "complexity_histogram.png")
plt.savefig(hist_path, dpi=300, bbox_inches="tight")

print(f"Saved: {hist_path}")