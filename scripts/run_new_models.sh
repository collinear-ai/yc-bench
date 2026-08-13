#!/usr/bin/env bash
# run_new_models.sh — Aug 2026 model sweep on the `default` preset (== published `medium`).
#
#   bash scripts/run_new_models.sh --dry-run          # show the plan, launch nothing
#   bash scripts/run_new_models.sh                    # 13 models x seeds 1 2 3, 8 at a time
#   bash scripts/run_new_models.sh --jobs 4 --seeds "1"
#   bash scripts/run_new_models.sh --models "x-ai/grok-4.5 moonshotai/kimi-k3"
#
# Idempotent: a (model, seed) whose result JSON already exists is skipped, so
# re-running after a crash or a credit top-up only fills in the gaps.

set -uo pipefail

CONFIG=default
SEEDS="1 2 3"
JOBS=8
DRY_RUN=0

MODELS=(
  "x-ai/grok-4.5"
  "x-ai/grok-4.6"
  "qwen/qwen3.8-max"
  "deepseek/deepseek-v4-pro-0813"
  "meta/muse-spark-1.1"
  "meta/muse-spark-1.2"
  "moonshotai/kimi-k3"
  "openai/gpt-5.6-sol"
  "openai/gpt-5.6-terra"
  "openai/gpt-5.6-luna"
  "anthropic/claude-opus-5"
  "google/gemini-3.6-flash"
  "thinkingmachines/inkling"
)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)  CONFIG="$2"; shift 2 ;;
    --seeds)   SEEDS="$2"; shift 2 ;;
    --jobs)    JOBS="$2"; shift 2 ;;
    --models)  read -r -a MODELS <<< "$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

mkdir -p logs db results

# main.py loads .env with override=False, so a stale OPENROUTER_API_KEY already in
# the environment would silently win. Force the .env key to take precedence.
if [[ -f .env ]]; then
  ENV_KEY=$(grep -oE 'sk-or-v1-[A-Za-z0-9]+' .env | head -1 || true)
  if [[ -n "${ENV_KEY:-}" ]]; then
    export OPENROUTER_API_KEY="$ENV_KEY"
    echo "Using OPENROUTER_API_KEY ${ENV_KEY:0:17}… (from .env, overriding environment)"
  fi
fi
BAL=$(curl -s --max-time 20 -H "Authorization: Bearer ${OPENROUTER_API_KEY:-}" \
  https://openrouter.ai/api/v1/credits | python3 -c "
import json,sys
d=json.load(sys.stdin)['data']
print(f\"{d['total_credits']-d['total_usage']:.2f}\")" 2>/dev/null || echo "?")
echo "OpenRouter balance remaining: \$$BAL"

launch() {  # launch <model> <seed>
  local model="$1" seed="$2"
  local slug="openrouter_${model//\//_}"
  local base="${CONFIG}_${seed}_${slug}"
  local result="results/yc_bench_result_${base}.json"

  if [[ -f "$result" ]]; then
    if grep -q '"terminal_reason": "error"' "$result"; then
      echo "  RETRY   $model seed=$seed (previous attempt errored)"
      rm -f "$result"
    else
      echo "  SKIP    $model seed=$seed (result exists)"
      return 1
    fi
  fi
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  WOULD RUN  $model seed=$seed  -> $result"
    return 1
  fi

  # Clear stale state: main.py resumes a run when the transcript AND session
  # files are both present, which would silently continue an old attempt.
  rm -f "db/${base}.db" "db/${base}.transcript.jsonl" "db/${base}.session.json"

  echo "  LAUNCH  $model seed=$seed  -> logs/${base}.log"
  .venv/bin/yc-bench run \
    --model "openrouter/${model}" \
    --seed "$seed" \
    --config "$CONFIG" \
    --no-live \
    > "logs/${base}.log" 2>&1 &
  return 0
}

echo "=== YC-Bench sweep: config=$CONFIG seeds=($SEEDS) models=${#MODELS[@]} jobs=$JOBS"
declare -a PIDS=() LABELS=()
for model in "${MODELS[@]}"; do
  for seed in $SEEDS; do
    while (( $(jobs -rp | wc -l) >= JOBS )); do wait -n 2>/dev/null || true; done
    if launch "$model" "$seed"; then
      PIDS+=($!); LABELS+=("$model seed=$seed")
    fi
  done
done

if [[ $DRY_RUN -eq 1 ]]; then echo "=== dry run, nothing launched"; exit 0; fi

echo ""; echo "=== ${#PIDS[@]} runs launched; waiting..."
FAILED=0
for i in "${!PIDS[@]}"; do
  if wait "${PIDS[$i]}"; then echo "  DONE  ${LABELS[$i]}"
  else echo "  FAIL  ${LABELS[$i]}"; FAILED=$((FAILED+1)); fi
done

echo ""
echo "=== complete: $(( ${#PIDS[@]} - FAILED )) ok, $FAILED failed"
grep -l '"terminal_reason": "error"' results/yc_bench_result_${CONFIG}_*.json 2>/dev/null \
  | sed 's/^/  ERRORED: /' || true
echo "Cost so far:"
python3 - <<'PY'
import json,glob
tot=0.0
for f in sorted(glob.glob("results/yc_bench_result_*.json")):
    try: d=json.load(open(f))
    except Exception: continue
    c=d.get("total_cost_usd",0.0); tot+=c
    print(f"  ${c:>8.2f}  {d.get('terminal_reason','?'):12} {d.get('turns_completed',0):>5} turns  {d.get('model','?')} seed={d.get('seed')}")
print(f"  ${tot:>8.2f}  TOTAL")
PY
