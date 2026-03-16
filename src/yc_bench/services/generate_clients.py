from __future__ import annotations

from dataclasses import dataclass, field

from ..config.schema import WorldConfig
from ..db.models.company import Domain
from .rng import RngStreams

_CLIENT_NAME_POOL = [
    "Nexus AI",
    "Vertex Labs",
    "Quantum Dynamics",
    "Atlas Computing",
    "Helix Systems",
    "Orion Data",
    "Cipher Corp",
    "Prism Analytics",
    "Nova Research",
    "Zenith Technologies",
    "Apex Robotics",
    "Stratos Cloud",
    "Vanguard ML",
    "Equinox Labs",
    "Cortex Intelligence",
]

_ALL_DOMAINS = list(Domain)


def _tier_from_multiplier(mult: float, cfg: WorldConfig) -> str:
    """Map reward multiplier to a visible tier label."""
    if mult < cfg.client_tier_premium_threshold:
        return "Standard"
    if mult < cfg.client_tier_enterprise_threshold:
        return "Premium"
    return "Enterprise"


@dataclass(frozen=True)
class GeneratedClient:
    name: str
    reward_multiplier: float  # per-client bonus applied on top of trust reward
    tier: str = "Standard"
    specialty_domains: list[str] = field(default_factory=list)


def generate_clients(*, run_seed: int, count: int, cfg: WorldConfig) -> list[GeneratedClient]:
    """Generate clients with seeded reward multipliers, tiers, and specialty domains."""
    if count <= 0:
        return []
    if count > len(_CLIENT_NAME_POOL):
        raise ValueError(f"count ({count}) exceeds available client names ({len(_CLIENT_NAME_POOL)})")

    streams = RngStreams(run_seed)
    rng = streams.stream("clients")
    names = rng.sample(_CLIENT_NAME_POOL, count)
    clients = []
    for name in names:
        mult = round(rng.triangular(cfg.client_reward_mult_low, cfg.client_reward_mult_high,
                                     cfg.client_reward_mult_mode), 2)
        tier = _tier_from_multiplier(mult, cfg)
        n_specialties = 1 if rng.random() < cfg.client_single_specialty_prob else 2
        specialties = [d.value for d in rng.sample(_ALL_DOMAINS, n_specialties)]
        clients.append(GeneratedClient(
            name=name,
            reward_multiplier=mult,
            tier=tier,
            specialty_domains=specialties,
        ))
    return clients


__all__ = ["GeneratedClient", "generate_clients"]
