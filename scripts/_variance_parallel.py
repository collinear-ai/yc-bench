"""Run N codegens × N trials per model in PARALLEL via multiprocessing.

Each worker process:
  1. Generates a fresh policy (its own LLM call)
  2. Saves it to policies/variance_<slug>_t<trial>.py
  3. Runs an episode against seed=1 with its own DB file (db_suffix=t<trial>)
  4. Writes results/variance_<slug>_t<trial>.json
"""

from __future__ import annotations

import json
import logging
import multiprocessing as mp
import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True), override=False)


def _trial(args: tuple[str, int]) -> dict:
    """Single (model, trial) — runs in its own process (spawn → fresh env)."""
    model, trial = args
    # Configure logging in the worker (each spawned proc starts blank).
    logging.basicConfig(
        level=logging.INFO,
        format=f"[t{trial:02d}] %(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    for n in ("litellm", "httpx", "httpcore", "LiteLLM"):
        logging.getLogger(n).setLevel(logging.WARNING)

    # Lazy import in worker so the parent doesn't pre-fork heavy modules.
    from yc_bench.policy_exp.codegen import generate_policy
    from yc_bench.policy_exp.runner import run_policy_episode

    slug = model.replace("/", "_").replace(":", "_")
    tag = f"t{trial:02d}"
    policy_path = Path("policies") / f"variance_{slug}_{tag}.py"
    out_path = Path("results") / f"variance_{slug}_{tag}.json"

    log = logging.getLogger(f"variance.{slug}.{tag}")
    log.info("BEGIN trial=%d model=%s", trial, model)
    t0 = time.time()

    try:
        # generate_policy writes to policies/<canonical-slug>.py — copy aside.
        gp = generate_policy(model, out_dir="policies", max_tokens=16000, overwrite=True)
        if policy_path.exists():
            policy_path.unlink()
        shutil.copy(gp.path, policy_path)
        log.info("CODEGEN ok tokens=%d cost=$%.3f", gp.completion_tokens, gp.cost_usd)
    except Exception as exc:
        result = {
            "model": model, "trial": trial, "status": "codegen_failed",
            "error": f"{type(exc).__name__}: {exc}",
            "wall_seconds": round(time.time() - t0, 2),
        }
        out_path.write_text(json.dumps(result, indent=2))
        log.error("CODEGEN failed: %s", exc)
        return result

    try:
        ep = run_policy_episode(
            model=model, seed=1, policy_path=policy_path,
            config_name="default", wall_timeout_seconds=600.0,
            max_commands=5000, max_helper_calls=200,
            db_suffix=tag,
        )
    except Exception as exc:
        result = {
            "model": model, "trial": trial, "status": "episode_crash",
            "error": f"{type(exc).__name__}: {exc}",
            "codegen_completion_tokens": gp.completion_tokens,
            "codegen_cost_usd": gp.cost_usd,
            "wall_seconds": round(time.time() - t0, 2),
        }
        out_path.write_text(json.dumps(result, indent=2))
        log.exception("EPISODE crash")
        return result

    result = {
        "model": model,
        "trial": trial,
        "status": "ok",
        "final_funds_cents": ep.final_funds_cents,
        "funds_delta_cents": ep.funds_delta_cents,
        "bankrupt": ep.bankrupt,
        "terminal_reason": ep.terminal_reason,
        "sim_commands": ep.sim_commands,
        "helper_calls": ep.helper_calls,
        "wall_seconds": ep.wall_seconds,
        "error": ep.error,
        "codegen_completion_tokens": gp.completion_tokens,
        "codegen_cost_usd": gp.cost_usd,
        "policy_path": str(policy_path),
    }
    out_path.write_text(json.dumps(result, indent=2))
    log.info(
        "DONE funds=$%.0f bankrupt=%s reason=%s cmds=%d wall=%.0fs",
        ep.final_funds_cents / 100, ep.bankrupt, ep.terminal_reason,
        ep.sim_commands, ep.wall_seconds,
    )
    return result


def main():
    MODELS = [
        "openrouter/anthropic/claude-opus-4.7",
        "openrouter/openai/gpt-5.5",
    ]
    N_TRIALS = 10

    # Spawn (Mac default) — each worker gets fresh env. 20 concurrent OK.
    ctx = mp.get_context("spawn")
    args = [(m, t) for m in MODELS for t in range(1, N_TRIALS + 1)]
    print(f"Launching {len(args)} workers ({len(MODELS)} models × {N_TRIALS} trials)…", flush=True)

    with ctx.Pool(processes=len(args)) as pool:
        results = pool.map(_trial, args)

    # Aggregate
    Path("results/variance_summary.json").write_text(json.dumps(results, indent=2))

    print()
    for model in MODELS:
        rows = [r for r in results if r["model"] == model]
        rows.sort(key=lambda r: r["trial"])
        print(f"\n========== {model} (n={len(rows)}) ==========")
        print(f"{'trial':>5} {'funds':>14} {'status':>14} {'reason':>14} {'cmds':>6} {'wall':>7}")
        for r in rows:
            funds = r.get("final_funds_cents")
            funds_str = f"${funds / 100:>12,.0f}" if isinstance(funds, int) else "       —     "
            reason = r.get("terminal_reason") or (
                r.get("error", "").split(":")[0] if r.get("error") else "—"
            )
            print(
                f"{r['trial']:>5} {funds_str} {r.get('status', '?')!s:>14} "
                f"{reason!s:>14} {r.get('sim_commands', '—')!s:>6} "
                f"{r.get('wall_seconds', 0):>6.0f}s"
            )
        # Quick stats
        funds_vals = [r["final_funds_cents"] for r in rows if isinstance(r.get("final_funds_cents"), int)]
        if funds_vals:
            n_bank = sum(1 for r in rows if r.get("bankrupt"))
            n_horizon = sum(1 for r in rows if r.get("terminal_reason") == "horizon_end")
            n_passive = sum(1 for r in rows if r.get("final_funds_cents") == 20000000 and not r.get("bankrupt"))
            print(
                f"  best=${max(funds_vals)/100:,.0f}  worst=${min(funds_vals)/100:,.0f}  "
                f"horizon_end={n_horizon}  bankrupt={n_bank}  passive=${n_passive}"
            )


if __name__ == "__main__":
    main()
