#!/usr/bin/env bash
set -euo pipefail

MODEL="gemini/gemini-3-flash-preview"
SEED=1
CONFIG="medium"
SLUG="${MODEL//\//_}"

# --- 1. Baseline run (on main branch, no client trust) ---
# git checkout main
rm -f "db/${CONFIG}_${SEED}_${SLUG}.db"
uv run yc-bench run --model "$MODEL" --seed "$SEED" --config "$CONFIG" --company-name BenchCo --start-date 2025-01-01 --no-live
uv run python scripts/plot_results.py "results/yc_bench_result_${CONFIG}_${SEED}_${SLUG}.json" --plot funds
uv run python scripts/plot_results.py "results/yc_bench_result_${CONFIG}_${SEED}_${SLUG}.json" --plot prestige

# --- 2. Client trust run (on vincent/client_trust branch) ---
# git checkout vincent/client_trust
rm -f "db/${CONFIG}_${SEED}_${SLUG}.db"
uv run yc-bench run --model "$MODEL" --seed "$SEED" --config "$CONFIG" --company-name BenchCo --start-date 2025-01-01 --no-live
uv run python scripts/plot_results.py "results/yc_bench_result_${CONFIG}_${SEED}_${SLUG}.json" --plot funds
uv run python scripts/plot_results.py "results/yc_bench_result_${CONFIG}_${SEED}_${SLUG}.json" --plot prestige
