"""Handler for bankruptcy events."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ...db.models.event import SimEvent


@dataclass
class BankruptcyResult:
    bankrupt: bool = True


def handle_bankruptcy(db: Session, event: SimEvent) -> BankruptcyResult:
    """Signal that the company has gone bankrupt."""
    return BankruptcyResult(bankrupt=True)
