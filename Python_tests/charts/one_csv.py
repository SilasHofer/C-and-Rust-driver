#!/usr/bin/env python3
"""
Create one single mapped CSV with aligned timestamps for:
C driver, Rust driver, and Source of Truth.

This version automatically detects the timestamp column.
"""

import sys
import pandas as pd
from datetime import timedelta

if len(sys.argv) != 4:
    print("Usage: python3 create_mapped_csv.py <c_csv> <rust_csv> <truth_csv>")
    print("Example: python3 create_mapped_csv.py c_readings_20260422_145008.csv rust_readings_20260422_145008.csv source_of_truth.csv")
    sys.exit(1)

c_file    = sys.argv[1]
rust_file = sys.argv[2]
truth_file = sys.argv[3]

print("=== Loading files ===")

def load_file(file, name):
    df = pd.read_csv(file)
    print(f"{name} columns: {list(df.columns)}")
    
    # Auto-detect timestamp column
    time_cols = [col for col in df.columns if any(x in col.lower() for x in ['time', 'timestamp', 'date'])]
    if time_cols:
        ts_col = time_cols[0]
        print(f"  → Using '{ts_col}' as timestamp column for {name}")
    else:
        ts_col = df.columns[0]
        print(f"  → No obvious time column found. Using first column '{ts_col}'")
    
    # Temperature is almost always the last column
    temp_col = df.columns[-1]
    print(f"  → Using '{temp_col}' as temperature column for {name}")
    
    # Keep only the two columns and rename
    df = df[[ts_col, temp_col]].rename(columns={ts_col: 'timestamp', temp_col: f'temp_{name}'})
    
    # Convert to datetime
    df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
    df = df.dropna(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)
    return df

# Load all three files
df_c    = load_file(c_file, "C")
df_rust = load_file(rust_file, "Rust")
df_truth = load_file(truth_file, "Truth")

print(f"\nLoaded → C: {len(df_c):,} rows | Rust: {len(df_rust):,} rows | Truth: {len(df_truth):,} rows")

# Use Truth as the reference timeline
df_aligned = df_truth.copy()

# Align C and Rust to Truth (nearest match within 2 seconds)
df_aligned = pd.merge_asof(
    df_aligned,
    df_c[['timestamp', 'temp_C']],
    on='timestamp',
    direction='nearest',
    tolerance=timedelta(seconds=2)
)

df_aligned = pd.merge_asof(
    df_aligned,
    df_rust[['timestamp', 'temp_Rust']],
    on='timestamp',
    direction='nearest',
    tolerance=timedelta(seconds=2)
)

# Final clean column order
df_aligned = df_aligned[['timestamp', 'temp_C', 'temp_Rust', 'temp_Truth']]

# Save
output_file = "mapped_temperatures_3way.csv"
df_aligned.to_csv(output_file, index=False)

print(f"\n✅ SUCCESS! Saved aligned file → {output_file}")
print(f"   Total rows: {len(df_aligned):,}")
print("\nFirst 10 rows:")
print(df_aligned.head(10))
print("\nYou can now use this file to create your 3-line chart.")