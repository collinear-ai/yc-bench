#!/bin/bash
# Run all models on medium config across seeds 1-3.
# Usage: bash run_all_models.sh [--seed 1] [--config medium]

set -e

CONFIG="${CONFIG:-medium}"
SEEDS="${SEEDS:-1 2 3}"

# Parse optional args
while [[ $# -gt 0 ]]; do
    case $1 in
        --seed) SEEDS="$2"; shift 2;;
        --config) CONFIG="$2"; shift 2;;
        *) echo "Unknown arg: $1"; exit 1;;
    esac
done

# Direct API models
DIRECT_MODELS=(
    "openai/gpt-5.4"
    "openai/gpt-5.4-mini"
    "openai/gpt-5.4-nano"
    "gemini/gemini-3.1-pro-preview"
    "gemini/gemini-3-flash-preview"
    "anthropic/claude-opus-4-6"
    "anthropic/claude-sonnet-4-6"
)

# OpenRouter models
OPENROUTER_MODELS=(
    "openrouter/qwen/qwen3.5-397b-a17b"
    "openrouter/minimax/minimax-m2.7"
    "openrouter/deepseek/deepseek-v3.2"
    "openrouter/z-ai/glm-5"
    "openrouter/moonshotai/kimi-k2.5"
    "openrouter/x-ai/grok-4.20-beta"
)

ALL_MODELS=("${DIRECT_MODELS[@]}" "${OPENROUTER_MODELS[@]}")

echo "=== YC-Bench Full Run ==="
echo "Config: $CONFIG"
echo "Seeds: $SEEDS"
echo "Models: ${#ALL_MODELS[@]}"
echo ""

# Run greedy bot baseline first
echo "--- Running greedy bot baseline ---"
for seed in $SEEDS; do
    echo "  greedy_bot | $CONFIG seed=$seed"
    uv run python scripts/bot_runner.py --bot greedy --config "$CONFIG" --seed "$seed"
done
echo ""

# Run all LLM models
for model in "${ALL_MODELS[@]}"; do
    for seed in $SEEDS; do
        # Derive DB name from model string (replace / with _)
        db_name=$(echo "$model" | tr '/' '_')
        db_path="db/${CONFIG}_${seed}_${db_name}.db"

        # Skip if result already exists
        result_file="results/yc_bench_result_${CONFIG}_${seed}_${db_name}.json"
        if [[ -f "$result_file" ]]; then
            echo "  SKIP $model seed=$seed (result exists)"
            continue
        fi

        echo "  RUN  $model | $CONFIG seed=$seed"
        rm -f "$db_path"
        uv run yc-bench run \
            --model "$model" \
            --seed "$seed" \
            --config "$CONFIG" \
            --no-live \
            2>&1 | tail -3
        echo ""
    done
done

echo "=== All runs complete ==="
