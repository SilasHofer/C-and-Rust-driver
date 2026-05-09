#!/bin/bash
#Thesis Benchmark Runner - Optimized for Statistical Rigor,
echo "Phase 1: Exploratory Distribution Data (N=1,000,000)"
#Purpose: To build the CDF and Boxplots shown in your draft.,
#We use many runs to capture OS jitter over a long period.,
python3 performance_test.py --both --runs 1000 --samples 1000 --log

echo "Phase 2: Formal Hypothesis Testing (Sequential)"
#Purpose: To reach a final 'Thesis Verdict' with high confidence.,
#We use a strict 99% confidence and a 0.1% MDE.,
python3 sequential_validation.py \
    --min-samples 5000 \
    --max-samples 50000 \
    --mde-pct 0.1 \
    --samples 1000 \
    --confidence 0.99 \
    --stability-window 5 \
    --warmup 500 \
    --min-looks 10