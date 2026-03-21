#!/bin/bash
# Run models on medium config across seeds — PARALLEL across models.
# Usage:
#   bash run_all_models.sh                    # all models, seeds 1-2
#   bash run_all_models.sh --seed "1"         # single seed
#   bash run_all_models.sh --config medium    # custom config

CONFIG="${CONFIG:-medium}"
SEEDS="1 2"

# Parse optional args
while [[ $# -gt 0 ]]; do
    case $1 in
        --seed) SEEDS="$2"; shift 2;;
        --config) CONFIG="$2"; shift 2;;
        *) echo "Unknown arg: $1"; exit 1;;
    esac
done

# Models to run (direct API)
MODELS=(
    "openai/gpt-5.4"
    "openai/gpt-5.4-mini"
    "openai/gpt-5.4-nano"
    "gemini/gemini-3.1-pro-preview"
    "gemini/gemini-3-flash-preview"
)

echo "=== YC-Bench Experiment Run (PARALLEL) ==="
echo "Config: $CONFIG"
echo "Seeds: $SEEDS"
echo "Models: ${#MODELS[@]}"
echo ""

mkdir -p db results plots

PIDS=()
LABELS=()

for model in "${MODELS[@]}"; do
    for seed in $SEEDS; do
        db_name=$(echo "$model" | tr '/' '_')
        result_file="results/yc_bench_result_${CONFIG}_${seed}_${db_name}.json"

        if [[ -f "$result_file" ]]; then
            echo "  SKIP $model seed=$seed (result exists)"
            continue
        fi

        echo "  LAUNCH $model | $CONFIG seed=$seed"
        db_path="db/${CONFIG}_${seed}_${db_name}.db"
        rm -f "$db_path"

        uv run yc-bench run \
            --model "$model" \
            --seed "$seed" \
            --config "$CONFIG" \
            --no-live \
            > "logs/${db_name}_seed${seed}.log" 2>&1 &

        PIDS+=($!)
        LABELS+=("$model seed=$seed")
    done
done

echo ""
echo "Launched ${#PIDS[@]} runs in parallel. Waiting..."
echo ""

# Wait for all and report
FAILED=0
for i in "${!PIDS[@]}"; do
    wait "${PIDS[$i]}"
    EXIT_CODE=$?
    if [[ $EXIT_CODE -eq 0 ]]; then
        echo "  DONE ${LABELS[$i]}"
    else
        echo "  FAIL ${LABELS[$i]} (exit $EXIT_CODE)"
        FAILED=$((FAILED + 1))
    fi
done

echo ""
echo "=== Complete: $((${#PIDS[@]} - FAILED)) succeeded, $FAILED failed ==="
echo ""
echo "Plot results with:"
echo "  uv run python scripts/plot_run.py results/yc_bench_result_${CONFIG}_*.json"
