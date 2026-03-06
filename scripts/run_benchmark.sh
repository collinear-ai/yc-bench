#!/usr/bin/env bash
# run_benchmark.sh — launch model benchmarks across multiple seeds in parallel
#
# Usage:
#   bash scripts/run_benchmark.sh [--seeds "1 2 3"] [--config NAME]
#
# Each (model × seed) pair gets its own process, db, log, and result file.

set -euo pipefail

SEEDS="1 2 3"
CONFIG=hard

while [[ $# -gt 0 ]]; do
  case "$1" in
    --seeds)  SEEDS="$2"; shift 2 ;;
    --config) CONFIG="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

MODELS=(
  "openrouter/google/gemini-3-flash-preview"
  "openrouter/minimax/minimax-m2.5"
  "openrouter/moonshotai/kimi-k2.5"
  "openrouter/deepseek/deepseek-chat"
)

mkdir -p logs db results

PIDS=()

for MODEL in "${MODELS[@]}"; do
  for SEED in $SEEDS; do
    SLUG=$(echo "$MODEL" | tr '/' '_')
    LOG="logs/${SEED}_${SLUG}.log"
    echo "Starting: seed=$SEED  $MODEL  →  $LOG"
    uv run yc-bench run \
      --model "$MODEL" \
      --seed "$SEED" \
      --config "$CONFIG" \
      > "$LOG" 2>&1 &
    PIDS+=($!)
  done
done

echo ""
echo "Launched ${#PIDS[@]} runs (${#MODELS[@]} models × $(echo $SEEDS | wc -w) seeds)"
echo "Tail a run:  tail -f logs/1_openrouter_google_gemini-3-flash-preview.log"
echo ""

FAILED=0
for PID in "${PIDS[@]}"; do
  if ! wait "$PID"; then
    FAILED=$((FAILED + 1))
  fi
done

echo ""
echo "All runs complete. Failed: $FAILED / ${#PIDS[@]}"
echo ""
echo "Results:"
for SEED in $SEEDS; do
  ls -lh results/yc_bench_result_${SEED}_*.json 2>/dev/null || true
done
