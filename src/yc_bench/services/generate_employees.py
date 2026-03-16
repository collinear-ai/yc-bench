from __future__ import annotations

from dataclasses import dataclass

from ..config.schema import WorldConfig
from ..db.models.company import Domain
from .rng import RngStreams, sample_right_skew_triangular_int

_ALL_DOMAINS = list(Domain)
_NUM_DOMAINS = len(_ALL_DOMAINS)

# Fixed tier composition for a 10-person startup.
# Repeated to cover any employee count via modular indexing.
_TIER_SEQUENCE = [
    "junior", "junior", "junior", "junior", "junior",
    "mid", "mid", "mid",
    "senior", "senior",
]


@dataclass(frozen=True)
class GeneratedEmployee:
    name: str
    work_hours_per_day: float
    salary_cents: int
    tier: str
    rates_by_domain: dict[str, float]


def _salary_tiers(cfg):
    return (cfg.salary_junior, cfg.salary_mid, cfg.salary_senior)


def _tier_by_name(cfg, tier_name):
    for tier in _salary_tiers(cfg):
        if tier.name == tier_name:
            return tier
    raise ValueError(f"Tier {tier_name} not found")


def _sample_salary_cents(rng, cfg, tier_name):
    tier = _tier_by_name(cfg, tier_name)
    return sample_right_skew_triangular_int(rng, tier.min_cents, tier.max_cents)


def _sample_domain_rates(rng, min_rate, max_rate):
    """Sample each domain's rate independently from min_rate to max_rate."""
    return [round(rng.uniform(min_rate, max_rate), 4) for _ in range(_NUM_DOMAINS)]


def generate_employees(*, run_seed, count, cfg):
    if count <= 0:
        return []

    streams = RngStreams(run_seed)

    # Build and shuffle tier assignments.
    tier_rng = streams.stream("tier_assignment")
    seq_len = len(_TIER_SEQUENCE)
    tiers = [_TIER_SEQUENCE[i % seq_len] for i in range(count)]
    tier_rng.shuffle(tiers)

    employees = []
    for idx in range(1, count + 1):
        rng = streams.stream(f"employee_{idx}")
        tier_name = tiers[idx - 1]
        tier_cfg = _tier_by_name(cfg, tier_name)

        domain_rates = _sample_domain_rates(rng, min_rate=tier_cfg.rate_min, max_rate=tier_cfg.rate_max)
        rates = dict(zip(_ALL_DOMAINS, domain_rates))

        employees.append(
            GeneratedEmployee(
                name=f"Emp_{idx}",
                work_hours_per_day=cfg.work_hours_per_day,
                salary_cents=_sample_salary_cents(rng, cfg, tier_name),
                tier=tier_name,
                rates_by_domain=rates,
            )
        )
    return employees


def build_employee_rows(*, run_seed, company_id, count, cfg):
    generated = generate_employees(run_seed=run_seed, count=count, cfg=cfg)
    employee_rows = []
    skill_rows = []

    for emp in generated:
        employee_rows.append(
            {
                "company_id": company_id,
                "name": emp.name,
                "work_hours_per_day": emp.work_hours_per_day,
                "salary_cents": emp.salary_cents,
            }
        )
        for domain, rate in emp.rates_by_domain.items():
            skill_rows.append(
                {
                    "_employee_name": emp.name,
                    "domain": domain,
                    "rate_domain_per_hour": rate,
                }
            )
    return employee_rows, skill_rows


__all__ = [
    "build_employee_rows",
    "GeneratedEmployee",
    "generate_employees",
]
