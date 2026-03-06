"""Pydantic models for all experiment configuration.

Every tunable parameter lives here. TOML files are validated against these
models — Pydantic catches typos and type errors at load time.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from .sampling import BetaDist, ConstantDist, NormalDist, TriangularDist, UniformDist, DistSpec  # noqa: F401


# ---------------------------------------------------------------------------
# Salary tier
# ---------------------------------------------------------------------------

class SalaryTierConfig(BaseModel):
    name: str
    share: float          # fraction of employees in this tier (all tiers must sum to 1.0)
    min_cents: int        # minimum monthly salary in cents
    max_cents: int        # maximum monthly salary in cents
    rate_min: float       # minimum skill rate (units/hour)
    rate_max: float       # maximum skill rate (units/hour)


# ---------------------------------------------------------------------------
# World distributions
#
# Each field names a random quantity in world generation and specifies which
# distribution family + parameters to use. Changing `type` switches families;
# changing parameters tunes the shape. See config/sampling.py for all families.
# ---------------------------------------------------------------------------

class WorldDists(BaseModel):
    # Prestige level required to accept a task (result cast to int).
    # Any DistSpec family works — e.g. constant for ablations, uniform for flat sampling.
    required_prestige: DistSpec = Field(
        default_factory=lambda: TriangularDist(low=1, high=10, mode=1)
    )
    # Base reward paid on task completion, in cents (result cast to int).
    reward_funds_cents: DistSpec = Field(
        default_factory=lambda: TriangularDist(low=500_000, high=10_000_000, mode=3_000_000)
    )
    # Number of domains required per task (result cast to int).
    domain_count: DistSpec = Field(
        default_factory=lambda: TriangularDist(low=1, high=3, mode=1)
    )
    # Work units required per domain (result cast to int).
    required_qty: DistSpec = Field(
        default_factory=lambda: TriangularDist(low=200, high=3000, mode=800)
    )
    # Prestige delta awarded per domain on task success.
    # Mean ~0.1: climbing from prestige 1→5 takes ~40 tasks.
    reward_prestige_delta: DistSpec = Field(
        default_factory=lambda: BetaDist(alpha=1.2, beta=2.8, scale=0.35, low=0.0, high=0.35)
    )
    # Skill rate boost fraction applied to each assigned employee on task success.
    skill_boost: DistSpec = Field(
        default_factory=lambda: NormalDist(mean=0.12, stdev=0.06, low=0.01, high=0.40)
    )


# ---------------------------------------------------------------------------
# Agent / LLM
# ---------------------------------------------------------------------------

class AgentConfig(BaseModel):
    model: str = "openrouter/z-ai/glm-5"
    temperature: float = 0.0
    top_p: float = 1.0
    request_timeout_seconds: float = 300.0
    retry_max_attempts: int = 3
    retry_backoff_seconds: float = 1.0
    # Conversation rounds kept in context before each API call; older rounds dropped.
    history_keep_rounds: int = 20
    # Optional system prompt override. None = use default from agent/prompt.py
    system_prompt: str | None = None


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

class LoopConfig(BaseModel):
    # Consecutive turns without `sim resume` before the loop forces a time-advance.
    auto_advance_after_turns: int = 10
    # Hard cap on total turns. null = unlimited.
    max_turns: int | None = None


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

class SimConfig(BaseModel):
    start_date: str = "2025-01-01"    # ISO 8601 (YYYY-MM-DD)
    horizon_years: int = 3
    company_name: str = "BenchCo"


# ---------------------------------------------------------------------------
# World generation
# ---------------------------------------------------------------------------

class WorldConfig(BaseModel):
    # --- Workforce ---
    num_employees: int = 10
    initial_funds_cents: int = 25_000_000    # $250,000
    initial_prestige_level: float = 1.0
    work_hours_per_day: float = 9.0

    # --- Market ---
    num_market_tasks: int = 500
    market_browse_default_limit: int = 50

    # --- Salary bump on task completion ---
    salary_bump_pct: float = 0.01    # 1% raise per assigned employee per completed task

    # --- Prestige mechanics ---
    prestige_max: float = 10.0
    prestige_min: float = 1.0
    penalty_fail_multiplier: float = 0.8
    penalty_cancel_multiplier: float = 1.2
    # Extra reward fraction per prestige level above 1.
    # At 0.55: prestige-8 tasks pay ~4.85x more than prestige-1.
    reward_prestige_scale: float = 0.3

    # Daily prestige decay per domain. Domains not exercised lose prestige
    # over time: -0.01/day → -0.3/month → untouched domain drops ~1 level
    # every ~3 months. Floored at prestige_min.
    prestige_decay_per_day: float = 0.01

    # Required qty scaling by prestige: qty *= 1 + prestige_qty_scale * (prestige - 1).
    # At 0.3: prestige-5 tasks need 2.2× the work of prestige-1 tasks.
    prestige_qty_scale: float = 0.3

    # --- Deadline computation ---
    deadline_qty_per_day: float = 150.0  # max per-domain qty / this = deadline days
    deadline_min_biz_days: int = 7

    # --- Progress milestones (fraction thresholds that trigger checkpoint events) ---
    task_progress_milestones: list[float] = Field(default_factory=lambda: [0.25, 0.5, 0.75])

    # --- Business hours ---
    workday_start_hour: int = 9
    workday_end_hour: int = 18

    # --- Distributions (shape of random draws during world generation) ---
    dist: WorldDists = Field(default_factory=WorldDists)

    # --- Salary tiers ---
    salary_junior: SalaryTierConfig = Field(
        default_factory=lambda: SalaryTierConfig(
            name="junior", share=0.50,
            min_cents=200_000, max_cents=400_000,
            rate_min=1.0, rate_max=4.0,
        )
    )
    salary_mid: SalaryTierConfig = Field(
        default_factory=lambda: SalaryTierConfig(
            name="mid", share=0.35,
            min_cents=600_000, max_cents=800_000,
            rate_min=4.0, rate_max=7.0,
        )
    )
    salary_senior: SalaryTierConfig = Field(
        default_factory=lambda: SalaryTierConfig(
            name="senior", share=0.15,
            min_cents=1_000_000, max_cents=1_500_000,
            rate_min=7.0, rate_max=10.0,
        )
    )

    @model_validator(mode="after")
    def _salary_shares_sum_to_one(self) -> WorldConfig:
        total = self.salary_junior.share + self.salary_mid.share + self.salary_senior.share
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"salary tier shares must sum to 1.0, got {total:.6f}")
        return self


# ---------------------------------------------------------------------------
# Top-level experiment
# ---------------------------------------------------------------------------

class ExperimentConfig(BaseModel):
    name: str = "default"
    description: str = ""
    agent: AgentConfig = Field(default_factory=AgentConfig)
    loop: LoopConfig = Field(default_factory=LoopConfig)
    sim: SimConfig = Field(default_factory=SimConfig)
    world: WorldConfig = Field(default_factory=WorldConfig)


__all__ = [
    "AgentConfig",
    "DistSpec",
    "ExperimentConfig",
    "LoopConfig",
    "SalaryTierConfig",
    "SimConfig",
    "WorldConfig",
    "WorldDists",
]
