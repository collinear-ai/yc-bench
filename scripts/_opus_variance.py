"""5 fresh Opus codegens + episodes on seed=1 to measure variance."""

from __future__ import annotations

import json
import logging
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True), override=False)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
for n in ("litellm", "httpx", "httpcore", "LiteLLM"):
    logging.getLogger(n).setLevel(logging.WARNING)

from yc_bench.policy_exp.codegen import generate_policy
from yc_bench.policy_exp.runner import run_policy_episode

MODEL = "openrouter/anthropic/claude-opus-4.7"
SEED = 1
N_TRIALS = 5
OUT = Path("results/opus_variance.json")
OUT.parent.mkdir(exist_ok=True)

results: list[dict] = []

for trial in range(1, N_TRIALS + 1):
    print(f"\n========== TRIAL {trial}/{N_TRIALS} ==========", flush=True)

    # Each trial gets its own archived policy file so we can compare them later.
    policy_path = Path("policies") / f"opus_variance_trial{trial}.py"
    if policy_path.exists():
        policy_path.unlink()

    gp = generate_policy(MODEL, out_dir="policies", max_tokens=16000, overwrite=True)
    shutil.copy(gp.path, policy_path)
    print(f"  codegen: {gp.completion_tokens} tok, ${gp.cost_usd:.3f}", flush=True)

    try:
        ep = run_policy_episode(
            model=MODEL,
            seed=SEED,
            policy_path=policy_path,
            config_name="default",
            wall_timeout_seconds=600.0,
            max_commands=5000,
            max_helper_calls=200,
        )
        results.append({
            "trial": trial,
            "final_funds_cents": ep.final_funds_cents,
            "bankrupt": ep.bankrupt,
            "terminal_reason": ep.terminal_reason,
            "sim_commands": ep.sim_commands,
            "wall_seconds": ep.wall_seconds,
            "codegen_completion_tokens": gp.completion_tokens,
            "codegen_cost_usd": gp.cost_usd,
            "error": ep.error,
        })
    except Exception as exc:
        print(f"  EPISODE CRASH: {type(exc).__name__}: {exc}", flush=True)
        results.append({
            "trial": trial,
            "final_funds_cents": None,
            "error": f"crash: {type(exc).__name__}: {exc}",
            "codegen_completion_tokens": gp.completion_tokens,
            "codegen_cost_usd": gp.cost_usd,
        })

    OUT.write_text(json.dumps(results, indent=2))

print("\n========== OPUS VARIANCE (seed=1, n=5) ==========", flush=True)
print(f"{'trial':>5} {'funds':>14} {'bankrupt':>9} {'reason':>14} {'cmds':>6} {'wall':>7} {'tok':>6}")
for r in results:
    funds = r.get("final_funds_cents")
    funds_str = f"${funds / 100:>12,.0f}" if isinstance(funds, int) else "       —     "
    reason = r.get("terminal_reason") or (r.get("error", "").split(":")[0] if r.get("error") else "—")
    print(
        f"{r['trial']:>5} {funds_str} {str(r.get('bankrupt', '—')):>9} "
        f"{str(reason):>14} {r.get('sim_commands', '—')!s:>6} "
        f"{r.get('wall_seconds', 0):>6.0f}s {r['codegen_completion_tokens']:>6}"
    )
