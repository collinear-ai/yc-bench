"""Greedy bot shim — delegates to bot_runner.py.

Usage:
  uv run python scripts/greedy_bot.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from bot_runner import CONFIGS, SEEDS, STRATEGIES, run_bot


def main():
    slug, strategy_fn = STRATEGIES["greedy"]
    print("Running greedy bot across all configs and seeds...\n")
    results = []

    for config_name in CONFIGS:
        for seed in SEEDS:
            print(f"  {config_name} seed={seed} ...", end=" ", flush=True)
            r = run_bot(config_name, seed, slug, strategy_fn)
            results.append(r)

            if r["bankrupt"]:
                tag = "BANKRUPT"
            elif r["final_balance_cents"] >= 1_000_000_00:
                tag = f"${r['final_balance_cents']/100:,.0f}"
            else:
                tag = f"${r['final_balance_cents']/100:,.0f}"

            print(f"{tag} | {r['tasks_completed']} OK, {r['tasks_failed']} fail | prestige {r['max_prestige']:.1f} | {r['turns']} turns")

    print(f"\n{'Config':<12} {'Seed':<5} {'Final Balance':>14} {'OK':>4} {'Fail':>5} {'Prestige':>9}")
    print("-" * 55)
    for r in results:
        fb = "BANKRUPT" if r["bankrupt"] else f"${r['final_balance_cents']/100:,.0f}"
        print(f"{r['config']:<12} {r['seed']:<5} {fb:>14} {r['tasks_completed']:>4} {r['tasks_failed']:>5} {r['max_prestige']:>8.1f}")

    bankrupt_count = sum(1 for r in results if r["bankrupt"])
    print(f"\nBankruptcies: {bankrupt_count}/{len(results)}")


if __name__ == "__main__":
    main()
