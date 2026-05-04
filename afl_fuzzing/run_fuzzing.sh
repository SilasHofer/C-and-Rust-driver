#!/bin/bash
# AFL++ launcher for comparable C vs Rust campaigns.
# Usage:
#   ./run_fuzzing.sh c [out_dir]
#   ./run_fuzzing.sh rust [out_dir]

set -euo pipefail

IN_DIR="${IN_DIR:-./in}"
NUM_CORES="${NUM_CORES:-7}"
EXEC_TIMEOUT_MS="${EXEC_TIMEOUT_MS:-40}"
CORE_START="${CORE_START:-1}"
MAX_TIME_SEC="${MAX_TIME_SEC:-0}"

TARGET_KIND="${1:-}"
if [[ -z "$TARGET_KIND" ]]; then
    echo "Usage: $0 <c|rust> [out_dir]"
    exit 1
fi

case "$TARGET_KIND" in
    c)
        TARGET="./fuzz_target"
        DEFAULT_OUT_DIR="./out_c_compare"
        ;;
    rust)
        TARGET="./afl_fuzzing/rust-fuzzing/target/debug/fuzz_target"
        DEFAULT_OUT_DIR="./out_rust_compare"
        ;;
    *)
        echo "Invalid target kind: $TARGET_KIND"
        echo "Usage: $0 <c|rust> [out_dir]"
        exit 1
        ;;
esac

OUT_DIR="${2:-$DEFAULT_OUT_DIR}"

if [[ ! -d "$IN_DIR" ]]; then
    echo "Input directory not found: $IN_DIR"
    exit 1
fi

if [[ ! -x "$TARGET" ]]; then
    echo "Target not found or not executable: $TARGET"
    exit 1
fi

if ! command -v afl-fuzz >/dev/null 2>&1; then
    echo "afl-fuzz not found in PATH"
    exit 1
fi

if ! command -v konsole >/dev/null 2>&1; then
    echo "konsole not found in PATH"
    exit 1
fi

AFL_TIME_LIMIT_OPT=""
if [[ "$MAX_TIME_SEC" =~ ^[0-9]+$ ]] && [[ "$MAX_TIME_SEC" -gt 0 ]]; then
    AFL_TIME_LIMIT_OPT="-V ${MAX_TIME_SEC}"
elif [[ "$MAX_TIME_SEC" != "0" ]]; then
    echo "MAX_TIME_SEC must be a non-negative integer"
    exit 1
fi

mkdir -p "$OUT_DIR/master"

start_instance() {
    local core="$1"
    shift
    konsole --hold -e bash -lc "echo [launcher] starting on core $core; exec taskset -c $core $*" &
}

start_instance "$CORE_START" env AFL_USE_ASAN=1 AFL_NO_AFFINITY=1 afl-fuzz -t "$EXEC_TIMEOUT_MS" $AFL_TIME_LIMIT_OPT -i "$IN_DIR" -o "$OUT_DIR/master" -M fuzzer01 -- "$TARGET" @@

for i in $(seq 2 "$NUM_CORES"); do
    out_sub="$OUT_DIR/$i"
    mkdir -p "$out_sub"

    core="$((CORE_START + i - 1))"
    start_instance "$core" env AFL_USE_ASAN=1 AFL_NO_AFFINITY=1 afl-fuzz -t "$EXEC_TIMEOUT_MS" $AFL_TIME_LIMIT_OPT -i "$IN_DIR" -o "$out_sub" -S "fuzzer0${i}" -- "$TARGET" @@
done

echo "Started AFL++ campaign"
echo "  target_kind      : $TARGET_KIND"
echo "  target           : $TARGET"
echo "  input_dir        : $IN_DIR"
echo "  output_dir       : $OUT_DIR"
echo "  num_cores        : $NUM_CORES"
echo "  exec_timeout_ms  : $EXEC_TIMEOUT_MS"
echo "  max_time_sec     : $MAX_TIME_SEC"
echo "  core_start       : $CORE_START"
echo "For a fair C vs Rust comparison, keep these settings identical between runs."