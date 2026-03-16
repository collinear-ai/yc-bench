import os

from .loader import load_config
from .schema import ExperimentConfig, AgentConfig, LoopConfig, SimConfig, WorldConfig, SalaryTierConfig


def get_world_config() -> WorldConfig:
    """Load WorldConfig from the active experiment (YC_BENCH_EXPERIMENT env var, default: 'default')."""
    return load_config(os.environ.get("YC_BENCH_EXPERIMENT", "default")).world


__all__ = [
    "load_config",
    "get_world_config",
    "ExperimentConfig",
    "AgentConfig",
    "LoopConfig",
    "SimConfig",
    "WorldConfig",
    "SalaryTierConfig",
]
