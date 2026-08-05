#!/usr/bin/env -S uv run --script
"""Policy-as-code experiment for yc-bench.

Instead of running the model as a turn-by-turn tool-using agent, we ask
each model ONCE to write a Python policy that drives the yc-bench CLI
via `env.run_command(...)`. The policy may consult a fixed cheap LLM
helper (`env.classify(...)`) for NLP-flavoured judgement (e.g.
adversarial-client detection). We then execute the policy across N
seeds and compare final funds.

Usage:
    uv run scripts/policy_experiment.py \\
        --models openrouter/anthropic/claude-haiku-4.5 openrouter/google/gemini-3-flash-preview \\
        --seeds 1 2 3 \\
        --config default

Per-run output JSON lands in results/policy_<config>_<seed>_<model_slug>.json.
Generated policy source lands in policies/<model_slug>.py.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

# Ensure src/ is importable when run from repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from dotenv import find_dotenv, load_dotenv  # noqa: E402

load_dotenv(find_dotenv(usecwd=True), override=False)

from yc_bench.policy_exp.api import DEFAULT_HELPER_MODEL  # noqa: E402
from yc_bench.policy_exp.codegen import generate_policy  # noqa: E402
from yc_bench.policy_exp.runner import run_policy_episode, write_result  # noqa: E402


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
    p.add_argument(
        "--models",
        nargs="+",
        required=True,
        help="Model IDs to test (LiteLLM format, e.g. openrouter/anthropic/claude-haiku-4.5).",
    )
    p.add_argument(
        "--seeds",
        type=int,
        nargs="+",
        default=[1, 2, 3],
        help="Seeds to run each policy against.",
    )
    p.add_argument("--config", default="default", help="Preset name or path to .toml.")
    p.add_argument(
        "--helper-model",
        default=DEFAULT_HELPER_MODEL,
        help=f"Cheap LLM used by env.classify (default: {DEFAULT_HELPER_MODEL}).",
    )
    p.add_argument(
        "--max-commands", type=int, default=5000,
        help="Per-episode budget on env.run_command calls.",
    )
    p.add_argument(
        "--max-helper-calls", type=int, default=200,
        help="Per-episode budget on env.classify calls.",
    )
    p.add_argument(
        "--wall-timeout", type=float, default=1800.0,
        help="Wall-clock seconds before a policy is killed.",
    )
    p.add_argument(
        "--reuse-policies", action="store_true",
        help="Skip codegen if policies/<slug>.py already exists.",
    )
    p.add_argument("--policies-dir", default="policies")
    p.add_argument("--results-dir", default="results")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    _setup_logging(args.verbose)

    # Make the helper model visible to PolicyEnv defaults too.
    os.environ["YC_BENCH_HELPER_MODEL"] = args.helper_model

    log = logging.getLogger("policy_experiment")
    log.info(
        "Models: %s | Seeds: %s | Config: %s | Helper: %s",
        args.models, args.seeds, args.config, args.helper_model,
    )

    summary = []

    # Phase 1: codegen — one call per model.
    policies = {}
    for model in args.models:
        try:
            gp = generate_policy(
                model,
                out_dir=args.policies_dir,
                overwrite=not args.reuse_policies,
            )
            policies[model] = gp
            log.info("Generated policy for %s → %s", model, gp.path)
        except Exception as exc:
            log.error("Codegen failed for %s: %s", model, exc)
            policies[model] = None

    # Phase 2: run each policy across all seeds.
    for model, gp in policies.items():
        if gp is None:
            for seed in args.seeds:
                summary.append({
                    "model": model, "seed": seed,
                    "final_funds_cents": None,
                    "error": "codegen_failed",
                })
            continue

        for seed in args.seeds:
            t0 = time.time()
            try:
                result = run_policy_episode(
                    model=model,
                    seed=seed,
                    policy_path=gp.path,
                    config_name=args.config,
                    helper_model=args.helper_model,
                    max_commands=args.max_commands,
                    max_helper_calls=args.max_helper_calls,
                    wall_timeout_seconds=args.wall_timeout,
                )
            except Exception as exc:
                log.exception("Run crashed for %s seed=%s", model, seed)
                summary.append({
                    "model": model, "seed": seed,
                    "final_funds_cents": None,
                    "error": f"runner_crash: {type(exc).__name__}: {exc}",
                    "wall_seconds": round(time.time() - t0, 2),
                })
                continue

            path = write_result(result, results_dir=args.results_dir)
            log.info(
                "Done %s seed=%s funds=$%.2f bankrupt=%s reason=%s commands=%d helpers=%d → %s",
                model, seed,
                result.final_funds_cents / 100,
                result.bankrupt,
                result.terminal_reason,
                result.sim_commands,
                result.helper_calls,
                path,
            )
            summary.append({
                "model": model,
                "seed": seed,
                "final_funds_cents": result.final_funds_cents,
                "bankrupt": result.bankrupt,
                "terminal_reason": result.terminal_reason,
                "sim_commands": result.sim_commands,
                "helper_calls": result.helper_calls,
                "wall_seconds": result.wall_seconds,
                "error": result.error,
            })

    summary_path = Path(args.results_dir) / f"policy_summary_{args.config}.json"
    summary_path.parent.mkdir(exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2))

    print()
    print(f"Summary written to {summary_path}")
    print()
    print(f"{'model':<55} {'seed':>4} {'funds':>14} {'cmds':>6} {'helpers':>8} {'reason':>14}")
    for row in summary:
        funds = row.get("final_funds_cents")
        funds_str = f"${funds / 100:>12,.0f}" if isinstance(funds, int) else "       —     "
        print(
            f"{row['model']:<55} {row['seed']:>4} {funds_str} "
            f"{row.get('sim_commands', '—'):>6} {row.get('helper_calls', '—'):>8} "
            f"{(row.get('terminal_reason') or row.get('error') or '—')!s:>14}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
