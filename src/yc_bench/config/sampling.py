"""Distribution specs and sampling.

Every random draw in world generation is described by a DistSpec. Callers
pass the spec from WorldConfig — changing distribution families or parameters
only requires a TOML edit, no code change.

Supported families
------------------
triangular  low, high, mode               → random.triangular
beta        alpha, beta, scale, low, high → scale × Beta(α,β), clamped
normal      mean, stdev, low, high        → gauss, clamped
uniform     low, high                     → random.uniform
constant    value                         → always returns value (useful for ablations)
"""

from __future__ import annotations

import random
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Distribution spec models (one per family)
# ---------------------------------------------------------------------------


class TriangularDist(BaseModel):
    type: Literal["triangular"] = "triangular"
    low: float
    high: float
    mode: float


class BetaDist(BaseModel):
    type: Literal["beta"] = "beta"
    alpha: float
    beta: float
    scale: float = 1.0
    low: float = 0.0
    high: float = 1.0


class NormalDist(BaseModel):
    type: Literal["normal"] = "normal"
    mean: float
    stdev: float
    low: float
    high: float


class UniformDist(BaseModel):
    type: Literal["uniform"] = "uniform"
    low: float
    high: float


class ConstantDist(BaseModel):
    type: Literal["constant"] = "constant"
    value: float


# Discriminated union — Pydantic picks the right model from the "type" field
DistSpec = Annotated[
    Union[TriangularDist, BetaDist, NormalDist, UniformDist, ConstantDist],
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------


def sample_from_spec(rng: random.Random, spec: DistSpec) -> float:
    """Draw one sample from the given distribution spec."""
    if isinstance(spec, TriangularDist):
        val = rng.triangular(spec.low, spec.high, spec.mode)
        return max(spec.low, min(spec.high, val))

    if isinstance(spec, BetaDist):
        val = spec.scale * rng.betavariate(spec.alpha, spec.beta)
        return round(max(spec.low, min(spec.high, val)), 4)

    if isinstance(spec, NormalDist):
        val = rng.gauss(spec.mean, spec.stdev)
        return round(max(spec.low, min(spec.high, val)), 4)

    if isinstance(spec, UniformDist):
        return rng.uniform(spec.low, spec.high)

    if isinstance(spec, ConstantDist):
        return spec.value

    raise TypeError(f"Unknown DistSpec type: {type(spec)}")


__all__ = [
    "BetaDist",
    "ConstantDist",
    "DistSpec",
    "NormalDist",
    "TriangularDist",
    "UniformDist",
    "sample_from_spec",
]
