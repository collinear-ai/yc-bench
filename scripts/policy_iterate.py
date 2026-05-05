#!/usr/bin/env -S uv run --script
"""Hill-climbing variant of policy_experiment: each model gets to revise
its own policy across N iterations, given the prior run's results.

Single model per process invocation (so multiple models can run in
parallel — each needs its own DATABASE_URL via its own process). For
parallel execution, launch one shell per model:

    uv run scripts/policy_iterate.py --model openrouter/anthropic/claude-opus-4.7 -n 10 \\
        > logs/iter_opus.log 2>&1 &
    uv run scripts/policy_iterate.py --model openrouter/google/gemini-3.1-pro-preview -n 10 \\
        > logs/iter_gemini.log 2>&1 &
    uv run scripts/policy_iterate.py --model openrouter/openai/gpt-5.5 -n 10 \\
        > logs/iter_gpt.log 2>&1 &
    wait
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from dotenv import find_dotenv, load_dotenv  # noqa: E402

load_dotenv(find_dotenv(usecwd=True), override=False)

from yc_bench.policy_exp.api import DEFAULT_HELPER_MODEL  # noqa: E402
from yc_bench.policy_exp.iterate import iterate_model  # noqa: E402


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="[%(asctime)s] %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("litellm", "httpx", "httpcore", "LiteLLM"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", required=True)
    p.add_argument("-n", "--iterations", type=int, default=10)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--config", default="default")
    p.add_argument("--helper-model", default=DEFAULT_HELPER_MODEL)
    p.add_argument("--max-commands", type=int, default=5000)
    p.add_argument("--max-helper-calls", type=int, default=200)
    p.add_argument("--wall-timeout", type=float, default=600.0)
    p.add_argument("--max-tokens", type=int, default=16000)
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--codegen-timeout", type=float, default=600.0)
    p.add_argument("--history-window", type=int, default=5,
                   help="How many prior iterations to feed into each prompt.")
    p.add_argument("--policies-dir", default="policies")
    p.add_argument("--results-dir", default="results")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    _setup_logging(args.verbose)

    os.environ["YC_BENCH_HELPER_MODEL"] = args.helper_model

    log = logging.getLogger("policy_iterate")
    log.info(
        "Iterating %s for %d rounds (seed=%d config=%s helper=%s wall=%ss)",
        args.model, args.iterations, args.seed, args.config, args.helper_model, args.wall_timeout,
    )

    records = iterate_model(
        model=args.model,
        n_iterations=args.iterations,
        seed=args.seed,
        config_name=args.config,
        helper_model=args.helper_model,
        wall_timeout_seconds=args.wall_timeout,
        max_commands=args.max_commands,
        max_helper_calls=args.max_helper_calls,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        codegen_timeout=args.codegen_timeout,
        policies_dir=args.policies_dir,
        results_dir=args.results_dir,
        history_window=args.history_window,
    )

    print()
    print(f"=== {args.model} — {len(records)} iterations ===")
    print(f"{'iter':>4} {'funds':>14} {'delta':>14} {'cmds':>6} {'wall':>7} {'reason':>14} {'cost':>8}")
    total_cost = 0.0
    for r in records:
        total_cost += r.codegen_cost_usd
        funds_str = f"${r.final_funds_cents / 100:>12,.0f}"
        delta_str = f"${r.funds_delta_cents / 100:>+12,.0f}"
        reason = r.terminal_reason or (r.error.split(":")[0] if r.error else "—")
        print(
            f"{r.iteration:>4} {funds_str} {delta_str} {r.sim_commands:>6} "
            f"{r.wall_seconds:>6.0f}s {reason!s:>14} ${r.codegen_cost_usd:>6.3f}"
        )
    print(f"  total codegen cost: ${total_cost:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
