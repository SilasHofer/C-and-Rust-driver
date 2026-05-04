#!/bin/bash
set -euo pipefail

# Wrapper for comparable Rust campaign.
./run_fuzzing.sh rust "${1:-./out_rust_compare}"
