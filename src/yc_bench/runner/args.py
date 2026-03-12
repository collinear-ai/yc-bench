from __future__ import annotations

from dataclasses import dataclass
import argparse
from pathlib import Path

@dataclass(frozen=True)
class RunArgs:
    model: str
    seed: int
    horizon_years: int | None   # None = defer to sim.horizon_years in config
    company_name: str
    start_date: str
    config_name: str = "default"
    no_live: bool = False
    max_episodes: int = 1

def build_parser():
    parser = argparse.ArgumentParser(
        prog="yc-bench run",
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--horizon-years", type=int, default=None,
                        help="Simulation horizon in years (default: read from config's sim.horizon_years)")
    parser.add_argument("--company-name", default="BenchCo")
    parser.add_argument("--start-date", default="2025-01-01", help="Simulation start date (YYYY-MM-DD)")
    parser.add_argument(
        "--config", dest="config_name", default="default",
        help="Preset name ('default', 'fast_test', 'high_reward') or path to a .toml file",
    )
    parser.add_argument(
        "--no-live", action="store_true", default=False,
        help="Disable the live terminal dashboard (show raw log output instead)",
    )
    parser.add_argument(
        "--max-episodes", type=int, default=1,
        help="Max episodes (restarts after bankruptcy with scratchpad carried over). Default: 1",
    )
    return parser

def parse_run_args(argv):
    parser = build_parser()
    ns = parser.parse_args(argv)
    _validate(ns, parser)
    return RunArgs(
        model=ns.model,
        seed=ns.seed,
        horizon_years=ns.horizon_years,
        company_name=ns.company_name,
        start_date=ns.start_date,
        config_name=ns.config_name,
        no_live=ns.no_live,
        max_episodes=ns.max_episodes,
    )

def _validate(ns, parser):
    if ns.horizon_years is not None and ns.horizon_years <= 0:
        parser.error("--horizon-years must be int > 0")
    if ns.max_episodes < 1:
        parser.error("--max-episodes must be int >= 1")

__all__ = ["RunArgs", "build_parser", "parse_run_args"]
