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
    loyalty: float = 0.0  # hidden loyalty score in [-1.0, 1.0]


def generate_clients(
    *, run_seed: int, count: int, cfg: WorldConfig
) -> list[GeneratedClient]:
    """Generate clients with seeded reward multipliers, tiers, specialty domains, and loyalty."""
    if count <= 0:
        return []
    if count > len(_CLIENT_NAME_POOL):
        raise ValueError(
            f"count ({count}) exceeds available client names ({len(_CLIENT_NAME_POOL)})"
        )

    streams = RngStreams(run_seed)
    rng = streams.stream("clients")
    names = rng.sample(_CLIENT_NAME_POOL, count)

    # Guarantee a fixed number of RATs: round(count * rat_fraction).
    # First N clients (after shuffle) are RATs with loyalty in [-1, -0.3],
    # remaining are loyal/neutral with loyalty in [-0.3, 1].
    n_rats = max(1, round(count * cfg.loyalty_rat_fraction))

    clients = []
    for i, name in enumerate(names):
        mult = round(
            rng.triangular(
                cfg.client_reward_mult_low,
                cfg.client_reward_mult_high,
                cfg.client_reward_mult_mode,
            ),
            2,
        )
        tier = _tier_from_multiplier(mult, cfg)
        n_specialties = 1 if rng.random() < cfg.client_single_specialty_prob else 2
        specialties = [d.value for d in rng.sample(_ALL_DOMAINS, n_specialties)]

        if i < n_rats:
            # RAT: loyalty in [-1.0, -0.3]
            loyalty = round(rng.uniform(-1.0, -0.3), 3)
            # RATs offer competitive rewards (top 30% range) to attract greedy agents
            mult = max(mult, cfg.client_reward_mult_high * 0.75)
        else:
            # Non-RAT: loyalty in [-0.3, 1.0]
            loyalty = round(rng.triangular(-0.3, 1.0, cfg.loyalty_mode), 3)

        clients.append(
            GeneratedClient(
                name=name,
                reward_multiplier=mult,
                tier=tier,
                specialty_domains=specialties,
                loyalty=loyalty,
            )
        )
    # Shuffle so RATs aren't always first in the list
    rng.shuffle(clients)
    return clients


__all__ = ["GeneratedClient", "generate_clients"]
