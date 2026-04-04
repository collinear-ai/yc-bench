from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass


def _stable_seed(run_seed, stream_key):
    raw = f"{run_seed}:{stream_key}".encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)


@dataclass(frozen=True)
class RngStreams:
    run_seed: int

    def stream(self, stream_key):
        return random.Random(_stable_seed(self.run_seed, stream_key))


def clamp_numeric(value, low, high):
    return max(low, min(high, value))


def sample_triangular_int(rng, low, high, mode):
    return int(clamp_numeric((round(rng.triangular(low, high, mode))), low, high))


def sample_right_skew_triangular_int(rng, low, high):
    return sample_triangular_int(rng, low, high, high)


def sample_normal_clamped_float(rng, mean, stdev, low, high):
    val = clamp_numeric(rng.gauss(mean, stdev), low, high)
    return round(val, 4)


def sample_beta_scaled(rng, alpha: float, beta: float, scale: float) -> float:
    """Sample from Beta(alpha, beta) multiplied by scale. Used for prestige reward deltas."""
    return round(scale * rng.betavariate(alpha, beta), 4)


def sample_left_skew_0_2(rng):
    """Backward-compat alias using default Beta(1.2, 2.8) * 2.0 params."""
    return sample_beta_scaled(rng, 1.2, 2.8, 2.0)


def sample_without_replacement(rng, population, k):
    return rng.sample(list(population), k)
