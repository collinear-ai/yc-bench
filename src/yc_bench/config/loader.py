"""Load ExperimentConfig from a built-in preset name or a user TOML file path.

Usage::

    from yc_bench.config import load_config

    cfg = load_config("default")             # built-in preset
    cfg = load_config("fast_test")           # built-in preset
    cfg = load_config("./my_run.toml")       # user file, absolute or relative path
    cfg = load_config("/abs/path/run.toml")  # user file, absolute path

User TOML files can inherit from a preset with ``extends``::

    # my_run.toml
    extends = "default"

    [agent]
    model = "openrouter/anthropic/claude-3.5-sonnet"

    [world]
    num_employees = 15

Environment variable overrides (applied last, highest priority)::

    YC_BENCH_MODEL                override agent.model
    YC_BENCH_TEMPERATURE          override agent.temperature
    YC_BENCH_TOP_P                override agent.top_p
    YC_BENCH_HISTORY_KEEP_ROUNDS  override agent.history_keep_rounds
    YC_BENCH_AUTO_ADVANCE_TURNS   override loop.auto_advance_after_turns
"""

from __future__ import annotations

import os
import tomllib
from importlib.resources import files
from pathlib import Path

from .schema import ExperimentConfig


def load_config(path_or_name: str = "default") -> ExperimentConfig:
    """Load a named preset or a TOML file path, apply env-var overrides."""
    raw = _read_raw(path_or_name)

    # Handle `extends = "preset_name"` inheritance
    if "extends" in raw:
        base_name = raw.pop("extends")
        base_raw = _read_preset(base_name)
        raw = _deep_merge(base_raw, raw)

    cfg = ExperimentConfig.model_validate(raw)
    return _apply_env_overrides(cfg)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_raw(path_or_name: str) -> dict:
    p = Path(path_or_name)
    # Treat as a file path if it has a .toml suffix or contains a path separator
    if p.suffix == ".toml" or os.sep in path_or_name or "/" in path_or_name:
        if not p.exists():
            raise FileNotFoundError(f"Config file not found: {p.resolve()}")
        return _read_file(p)
    return _read_preset(path_or_name)


def _read_preset(name: str) -> dict:
    try:
        resource = files("yc_bench.config.presets") / f"{name}.toml"
        with resource.open("rb") as f:
            return tomllib.load(f)
    except (FileNotFoundError, TypeError):
        available = [
            p.name.replace(".toml", "")
            for p in Path(__file__).parent.joinpath("presets").glob("*.toml")
        ]
        raise ValueError(
            f"Unknown preset '{name}'. Available presets: {available}. "
            "Pass a file path ending in .toml for a custom config."
        )


def _read_file(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override on top of base. Override wins on conflicts."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _apply_env_overrides(cfg: ExperimentConfig) -> ExperimentConfig:
    """YC_BENCH_* env vars always win, regardless of config file."""
    agent_updates: dict = {}
    loop_updates: dict = {}

    if v := os.environ.get("YC_BENCH_MODEL"):
        agent_updates["model"] = v
    if v := os.environ.get("YC_BENCH_TEMPERATURE"):
        agent_updates["temperature"] = float(v)
    if v := os.environ.get("YC_BENCH_TOP_P"):
        agent_updates["top_p"] = float(v)
    if v := os.environ.get("YC_BENCH_HISTORY_KEEP_ROUNDS"):
        agent_updates["history_keep_rounds"] = int(v)
    if v := os.environ.get("YC_BENCH_AUTO_ADVANCE_TURNS"):
        loop_updates["auto_advance_after_turns"] = int(v)

    if agent_updates:
        cfg = cfg.model_copy(
            update={"agent": cfg.agent.model_copy(update=agent_updates)}
        )
    if loop_updates:
        cfg = cfg.model_copy(update={"loop": cfg.loop.model_copy(update=loop_updates)})
    return cfg


__all__ = ["load_config"]
