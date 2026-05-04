#!/bin/bash
set -euo pipefail

# Wrapper for comparable C campaign.
./run_fuzzing.sh c "${1:-./out_c_compare}"
