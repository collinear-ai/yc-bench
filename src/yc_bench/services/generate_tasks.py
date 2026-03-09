from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..config.sampling import sample_from_spec
from ..config.schema import WorldConfig
from ..db.models.company import Domain
from .rng import RngStreams, sample_without_replacement


@dataclass(frozen=True)
class GeneratedTask:
    title: str
    required_prestige: int
    reward_funds_cents: int
    reward_prestige_delta: float
    skill_boost_pct: float
    status: str
    company_id: Any | None
    accepted_at: datetime | None
    deadline: datetime | None
    completed_at: datetime | None
    success: bool | None
    progress_milestone_pct: int
    requirements: dict[str, int]
    client_index: int = 0
    required_trust: int = 0


# First 10 market tasks are forced to prestige 1 to guarantee a
# bootstrapping path regardless of the prestige distribution.
_STRATIFIED_PRESTIGE = [1, 1, 1, 1, 1, 1, 1, 1, 1, 1]

_ALL_DOMAINS = list(Domain)


def _sample_required_prestige(rng, cfg, index=None):
    if index is not None and index < len(_STRATIFIED_PRESTIGE):
        return _STRATIFIED_PRESTIGE[index]
    return int(sample_from_spec(rng, cfg.dist.required_prestige))


def _sample_reward_funds_cents(rng, cfg, prestige=1):
    base = int(sample_from_spec(rng, cfg.dist.reward_funds_cents))
    # Scale reward by prestige: higher-prestige tasks pay proportionally more
    return int(base * (1 + cfg.reward_prestige_scale * (prestige - 1)))


def _sample_reward_prestige_delta(rng, cfg):
    return sample_from_spec(rng, cfg.dist.reward_prestige_delta)


def _sample_skill_boost_pct(rng, cfg):
    return sample_from_spec(rng, cfg.dist.skill_boost)


def _sample_domain_count(rng, cfg):
    return int(sample_from_spec(rng, cfg.dist.domain_count))


def _sample_required_qty(rng, cfg):
    return int(sample_from_spec(rng, cfg.dist.required_qty))


def _sample_requirements(rng, cfg, prestige=1):
    k = _sample_domain_count(rng, cfg)
    picked_domains = sample_without_replacement(rng, _ALL_DOMAINS, k)
    scale = 1 + cfg.prestige_qty_scale * (prestige - 1)
    return {domain: int(_sample_required_qty(rng, cfg) * scale) for domain in picked_domains}


def _sample_required_trust(rng, cfg):
    """80% of tasks have required_trust=0, 20% sample from dist.required_trust."""
    if rng.random() >= cfg.trust_exclusive_task_fraction:
        return 0
    return max(1, int(sample_from_spec(rng, cfg.dist.required_trust)))


def _make_task(rng, cfg, prestige, serial, requirements, client_index=0, required_trust=0):
    reward = _sample_reward_funds_cents(rng, cfg, prestige=prestige)
    # Trust-gated tasks get a reward boost
    if required_trust > 0:
        reward = int(reward * 1.3)
    return GeneratedTask(
        title=f"Task-{serial}",
        required_prestige=prestige,
        reward_funds_cents=reward,
        reward_prestige_delta=_sample_reward_prestige_delta(rng, cfg),
        skill_boost_pct=_sample_skill_boost_pct(rng, cfg),
        status="market",
        company_id=None,
        accepted_at=None,
        deadline=None,
        completed_at=None,
        success=None,
        progress_milestone_pct=0,
        requirements=requirements,
        client_index=client_index,
        required_trust=required_trust,
    )


def generate_tasks(*, run_seed, count, cfg=None):
    if cfg is None:
        cfg = WorldConfig()
    if count <= 0:
        return []

    streams = RngStreams(run_seed)
    num_clients = cfg.num_clients if cfg.num_clients > 0 else 1
    out = []
    for idx in range(1, count + 1):
        rng = streams.stream(f"task_{idx}")
        prestige = _sample_required_prestige(rng, cfg, index=idx - 1)
        requirements = _sample_requirements(rng, cfg, prestige=prestige)
        client_index = (idx - 1) % num_clients
        required_trust = _sample_required_trust(rng, cfg)
        out.append(_make_task(rng, cfg, prestige, serial=idx, requirements=requirements,
                              client_index=client_index, required_trust=required_trust))
    return out


def build_task_rows(*, run_seed, count, cfg=None):
    generated = generate_tasks(run_seed=run_seed, count=count, cfg=cfg)
    task_rows = []
    requirement_rows = []

    for task in generated:
        task_rows.append({
            "title": task.title,
            "required_prestige": task.required_prestige,
            "reward_funds_cents": task.reward_funds_cents,
            "reward_prestige_delta": task.reward_prestige_delta,
            "skill_boost_pct": task.skill_boost_pct,
            "status": task.status,
            "company_id": task.company_id,
            "accepted_at": task.accepted_at,
            "deadline": task.deadline,
            "completed_at": task.completed_at,
            "success": task.success,
            "progress_milestone_pct": task.progress_milestone_pct,
            "client_index": task.client_index,
            "required_trust": task.required_trust,
        })
        for domain, qty in task.requirements.items():
            requirement_rows.append({
                "_task_title": task.title,
                "domain": domain,
                "required_qty": qty,
                "completed_qty": 0,
            })
    return task_rows, requirement_rows


def generate_replacement_task(*, run_seed, replenish_counter, replaced_prestige, replaced_client_index=0, cfg=None):
    """Generate a replacement task with the same prestige and client as the accepted task."""
    if cfg is None:
        cfg = WorldConfig()
    streams = RngStreams(run_seed)
    rng = streams.stream(f"replenish_{replenish_counter}")
    requirements = _sample_requirements(rng, cfg, prestige=replaced_prestige)
    required_trust = _sample_required_trust(rng, cfg)
    return _make_task(rng, cfg, replaced_prestige, serial=replenish_counter, requirements=requirements,
                      client_index=replaced_client_index, required_trust=required_trust)


__all__ = [
    "build_task_rows",
    "generate_replacement_task",
    "generate_tasks",
    "GeneratedTask",
]
