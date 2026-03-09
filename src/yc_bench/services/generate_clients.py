from __future__ import annotations

from dataclasses import dataclass

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


@dataclass(frozen=True)
class GeneratedClient:
    name: str


def generate_clients(*, run_seed: int, count: int) -> list[GeneratedClient]:
    if count <= 0:
        return []
    if count > len(_CLIENT_NAME_POOL):
        raise ValueError(f"count ({count}) exceeds available client names ({len(_CLIENT_NAME_POOL)})")

    streams = RngStreams(run_seed)
    rng = streams.stream("clients")
    names = rng.sample(_CLIENT_NAME_POOL, count)
    return [GeneratedClient(name=name) for name in names]


__all__ = ["GeneratedClient", "generate_clients"]
