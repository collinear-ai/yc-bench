"""Handler for horizon_end events."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ...db.models.event import SimEvent


@dataclass
class HorizonEndResult:
    reached: bool = True


def handle_horizon_end(db: Session, event: SimEvent) -> HorizonEndResult:
    """Signal that the simulation horizon has been reached."""
    return HorizonEndResult(reached=True)
