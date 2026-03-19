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
        default_factory=lambda: TriangularDist(low=300_000, high=4_000_000, mode=1_400_000)
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
    # Trust level required to accept exclusive tasks (sampled for ~20% of tasks).
    required_trust: DistSpec = Field(
        default_factory=lambda: TriangularDist(low=1, high=5, mode=2)
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
    """All world-generation parameters.

    No defaults — every field must be set explicitly in the TOML preset.
    This prevents silent drift between schema.py and the preset files.
    """

    # --- Workforce ---
    num_employees: int
    initial_funds_cents: int
    initial_prestige_level: float
    work_hours_per_day: float

    # --- Market ---
    num_market_tasks: int
    market_browse_default_limit: int

    # --- Salary bump on task completion ---
    salary_bump_pct: float
    salary_max_cents: int
    skill_rate_max: float

    # --- Prestige mechanics ---
    prestige_max: float
    prestige_min: float
    penalty_fail_multiplier: float
    penalty_fail_funds_pct: float = 0.0  # fraction of advertised reward deducted on failure
    penalty_cancel_multiplier: float
    reward_prestige_scale: float
    prestige_decay_per_day: float

    # --- Client trust (intuitive knobs) ---
    num_clients: int
    trust_max: float
    trust_build_rate: float
    trust_fragility: float
    trust_focus_pressure: float
    trust_reward_ceiling: float
    trust_work_reduction_max: float
    trust_gating_fraction: float

    # --- Client loyalty (adversarial clients) ---
    loyalty_rat_fraction: float = 0.15
    loyalty_severity: float = 0.5
    loyalty_reveal_trust: float = 2.0

    # --- Derived trust params (computed from knobs above, do not set directly) ---
    trust_min: float = 0.0
    trust_gain_base: float = 0.0
    trust_gain_diminishing_power: float = 1.5
    trust_fail_penalty: float = 0.0
    trust_cancel_penalty: float = 0.0
    trust_decay_per_day: float = 0.0
    trust_cross_client_decay: float = 0.0
    trust_base_multiplier: float = 0.50
    trust_reward_scale: float = 0.0
    trust_reward_threshold: float = 0.0
    trust_reward_ramp: float = 0.0
    trust_level_reward_scale: float = 3.0
    trust_level_max_required: int = 4
    trust_gated_reward_boost: float = 0.15
    client_reward_mult_low: float = 0.7
    client_reward_mult_high: float = 2.5
    client_reward_mult_mode: float = 1.0
    client_single_specialty_prob: float = 0.6
    client_tier_premium_threshold: float = 1.0
    client_tier_enterprise_threshold: float = 1.7
    task_specialty_domain_bias: float = 0.7

    # --- Derived loyalty params (computed from knobs above, do not set directly) ---
    loyalty_mode: float = 0.61
    scope_creep_max: float = 0.35
    dispute_clawback_max: float = 0.40
    dispute_prob_max: float = 0.25

    # --- Task scaling ---
    prestige_qty_scale: float
    deadline_qty_per_day: float
    deadline_min_biz_days: int

    # --- Progress milestones ---
    task_progress_milestones: list[float]

    # --- Business hours ---
    workday_start_hour: int
    workday_end_hour: int

    # --- Distributions ---
    dist: WorldDists = Field(default_factory=WorldDists)

    # --- Salary tiers ---
    salary_junior: SalaryTierConfig
    salary_mid: SalaryTierConfig
    salary_senior: SalaryTierConfig

    @model_validator(mode="after")
    def _derive_trust_params(self) -> WorldConfig:
        """Derive detailed trust parameters from the intuitive knobs.

        Derivation preserves default behavior: trust_build_rate=20, fragility=0.5,
        focus_pressure=0.5, reward_ceiling=2.6 produce the same values as the
        original hardcoded defaults.
        """
        # trust_build_rate → gain_base
        # Approximate: gain_base ≈ trust_max × 1.6 / build_rate
        # At default (20): 5.0 × 1.6 / 20 = 0.40
        self.trust_gain_base = self.trust_max * 1.6 / self.trust_build_rate

        # trust_fragility → fail_penalty, cancel_penalty, decay_per_day
        # At 0.5: fail=0.3, cancel=0.5, decay=0.015
        self.trust_fail_penalty = self.trust_fragility * 0.6
        self.trust_cancel_penalty = self.trust_fragility * 1.0
        self.trust_decay_per_day = self.trust_fragility * 0.03

        # trust_focus_pressure → cross_client_decay
        # At 0.5: cross_client_decay = 0.03
        self.trust_cross_client_decay = self.trust_focus_pressure * 0.06

        # trust_reward_ceiling → reward_scale
        # ceiling = base_multiplier + ref_mult² × scale × trust_max
        # Using Premium reference (mult≈1.3): scale = (ceiling - 0.50) / (1.69 × trust_max)
        ref_mult_sq = 1.69  # 1.3²
        self.trust_reward_scale = (
            (self.trust_reward_ceiling - self.trust_base_multiplier)
            / (ref_mult_sq * self.trust_max)
        )

        # trust_gating_fraction → threshold + ramp
        # At 0.2: threshold=0.6, ramp=0.4 (top 40% CAN require, effective ~20%)
        self.trust_reward_threshold = max(0.0, 1.0 - 2.0 * self.trust_gating_fraction)
        self.trust_reward_ramp = min(1.0, 2.0 * self.trust_gating_fraction)

        # loyalty params
        # loyalty_mode: triangular(-1, 1, mode) where mode produces ~rat_fraction below -0.3
        self.loyalty_mode = 1.0 - 2.6 * self.loyalty_rat_fraction
        self.scope_creep_max = self.loyalty_severity * 1.00
        self.dispute_clawback_max = self.loyalty_severity * 1.20
        self.dispute_prob_max = self.loyalty_severity * 1.00

        return self

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
