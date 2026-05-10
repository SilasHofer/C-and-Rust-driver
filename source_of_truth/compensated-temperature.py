#!/usr/bin/env python

import argparse
import csv
import os
import time
from datetime import datetime

from smbus2 import SMBus

from bme280 import BME280

# Check if CSV file exists and read start time
csv_filename = "temperature_readings.csv"
file_exists = os.path.exists(csv_filename)

if file_exists:
    with open(csv_filename, "r") as f:
        first_line = f.readline().strip()
        if first_line.startswith("START,"):
            original_start_str = first_line.split(",", 1)[1]
            original_start = datetime.fromisoformat(original_start_str)
            start_time = original_start.timestamp()
            print(f"Resuming from original start time: {original_start_str}")
        else:
            # No START header, use current time
            start_time = time.time()
else:
    # First run
    start_time = time.time()

# Open CSV file
csv_file = open(csv_filename, "a", newline="")

# If file didn't exist, write START header
if not file_exists:
    csv_file.write(f"START,{datetime.now().isoformat()}\n")

csv_writer = csv.writer(csv_file)

print(
    """compensated-temperature.py - Use the CPU temperature to compensate temperature
readings from the BME280 sensor. Method adapted from Initial State's Enviro pHAT
review: https://medium.com/@InitialState/tutorial-review-enviro-phat-for-raspberry-pi-4cd6d8c63441

Press Ctrl+C to exit!

"""
)

# Parse command line arguments
parser = argparse.ArgumentParser(description="BME280 temperature compensation with optional runtime limit")
parser.add_argument("--duration", type=int, default=None, help="Runtime duration in seconds (None for infinite)")
parser.add_argument("--interval", type=float, default=1.0, help="Sampling interval in seconds (default: 1.0, use 0 for full speed)")
args = parser.parse_args()

# Initialise the BME280
bus = SMBus(1)
bme280 = BME280(i2c_dev=bus)

read_errors = 0

try:
    while True:
        if args.duration is not None:
            elapsed_time = time.time() - start_time
            if elapsed_time >= args.duration:
                print(f"Runtime duration of {args.duration} seconds reached. Exiting.")
                break
        
        try:
            temp = bme280.get_temperature()
            read_errors = 0  # Reset error counter on success

            timestamp = datetime.now().isoformat()
            csv_writer.writerow([timestamp, f"{temp:.2f}"])
            csv_file.flush()
        except Exception as e:
            read_errors += 1
            print(f"Sensor read failed (attempt {read_errors}): {e}")
            if read_errors > 5:
                print("Too many consecutive errors. Exiting.")
                break
            time.sleep(1)  # Wait before retrying
            continue

        if args.interval > 0:
            time.sleep(args.interval)
finally:
    csv_file.close()
