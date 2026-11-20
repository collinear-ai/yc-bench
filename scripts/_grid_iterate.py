"""Full grid: 7 models × 3 configs × 3 seeds = 63 runs × 5 iterations,
32K-token codegen budget, v2 rich-feedback iteration loop.

Runs with concurrency=40 — 23 will queue and start as workers free up.
"""

from __future__ import annotations

import json
import logging
import multiprocessing as mp
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True), override=False)


MODELS = [
    "openrouter/anthropic/claude-opus-4.7",
    "openrouter/openai/gpt-5.5",
    "openrouter/google/gemini-3.1-pro-preview",
    "openrouter/z-ai/glm-5.1",
    "openrouter/moonshotai/kimi-k2.6",          # corrected ID
    "openrouter/qwen/qwen3.5-397b-a17b",        # corrected ID
    "openrouter/minimax/minimax-m2.7",          # latest
]
CONFIGS = ["easy", "default", "hard"]
SEEDS = [1, 2, 3]
ITERS = 10
MAX_TOKENS = 32000
WALL_TIMEOUT = 600.0
CONCURRENCY = 40
HISTORY_WINDOW = 10                              # all 9 priors visible at iter 10

GRID_DIR = Path("results_grid_v2")
POL_DIR = Path("policies_grid_v2")


def _run_one(args: tuple[str, str, int]) -> dict:
    model, cfg, seed = args
    slug = model.replace("/", "_").replace(":", "_")
    tag = f"{cfg}_s{seed}_{slug}"

    logging.basicConfig(
        level=logging.INFO,
        format=f"[{tag}] %(asctime)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    for n in ("litellm", "httpx", "httpcore", "LiteLLM"):
        logging.getLogger(n).setLevel(logging.WARNING)

    from yc_bench.policy_exp.iterate import iterate_model

    pol_dir = POL_DIR / cfg / f"seed{seed}"
    res_dir = GRID_DIR / cfg / f"seed{seed}"
    pol_dir.mkdir(parents=True, exist_ok=True)
    res_dir.mkdir(parents=True, exist_ok=True)

    log = logging.getLogger("grid")
    log.info("BEGIN %s", tag)
    t0 = time.time()
    try:
        records = iterate_model(
            model=model,
            n_iterations=ITERS,
            seed=seed,
            config_name=cfg,
            wall_timeout_seconds=WALL_TIMEOUT,
            max_commands=5000,
            max_helper_calls=200,
            max_tokens=MAX_TOKENS,
            policies_dir=pol_dir,
            results_dir=res_dir,
            history_window=HISTORY_WINDOW,
        )
    except Exception as exc:
        log.exception("Grid run crashed for %s", tag)
        return {
            "model": model, "config": cfg, "seed": seed,
            "status": "crash", "error": f"{type(exc).__name__}: {exc}",
            "wall": round(time.time() - t0, 2),
        }

    summary = {
        "model": model, "config": cfg, "seed": seed,
        "status": "ok",
        "n_iters": len(records),
        "wins": sum(1 for r in records if r.terminal_reason == "horizon_end"),
        "bankruptcies": sum(1 for r in records if r.bankrupt),
        "best_funds_cents": max(
            (r.final_funds_cents for r in records), default=0
        ),
        "trajectories": [
            {
                "iter": r.iteration,
                "funds": r.final_funds_cents,
                "bankrupt": r.bankrupt,
                "terminal": r.terminal_reason,
                "error": r.error,
            }
            for r in records
        ],
        "total_codegen_cost_usd": round(
            sum(r.codegen_cost_usd for r in records), 4
        ),
        "wall": round(time.time() - t0, 2),
    }
    log.info(
        "DONE %s wins=%d/%d best=$%.0f cost=$%.2f wall=%.0fs",
        tag, summary["wins"], summary["n_iters"],
        summary["best_funds_cents"] / 100,
        summary["total_codegen_cost_usd"], summary["wall"],
    )
    return summary


def main():
    GRID_DIR.mkdir(exist_ok=True)
    POL_DIR.mkdir(exist_ok=True)

    args_list = [(m, c, s) for m in MODELS for c in CONFIGS for s in SEEDS]
    print(
        f"Launching {len(args_list)} runs "
        f"({len(MODELS)} models × {len(CONFIGS)} configs × {len(SEEDS)} seeds), "
        f"{ITERS} iters each, max_tokens={MAX_TOKENS}, concurrency={CONCURRENCY}",
        flush=True,
    )

    ctx = mp.get_context("spawn")
    with ctx.Pool(processes=CONCURRENCY) as pool:
        results = pool.map(_run_one, args_list)

    summary_path = GRID_DIR / "grid_summary.json"
    summary_path.write_text(json.dumps(results, indent=2, default=str))

    # Tabular print
    print()
    print(f"{'config':>8} {'seed':>4} {'model':<55} {'wins':>5} {'best':>14} {'cost':>7} {'wall':>6}")
    for r in sorted(results, key=lambda r: (r["config"], r["seed"], r["model"])):
        if r.get("status") != "ok":
            print(f"{r['config']:>8} {r['seed']:>4} {r['model']:<55} CRASH   {r.get('error', '')[:30]}")
            continue
        funds = r.get("best_funds_cents", 0)
        print(
            f"{r['config']:>8} {r['seed']:>4} {r['model']:<55} "
            f"{r['wins']:>2}/{r['n_iters']:<2} "
            f"${funds / 100:>11,.0f} ${r['total_codegen_cost_usd']:>5.2f} {r['wall']:>5.0f}s"
        )

    total_cost = sum(r.get("total_codegen_cost_usd", 0) for r in results)
    total_wins = sum(r.get("wins", 0) for r in results)
    print(f"\nTotal wins: {total_wins}    Total cost: ${total_cost:.2f}")


if __name__ == "__main__":
    main()
