"""Handler for payment_dispute events.

A payment dispute claws back a fraction of a previously paid task reward.
Scheduled by task_complete for RAT clients at high trust.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from ...db.models.company import Company
from ...db.models.event import SimEvent
from ...db.models.ledger import LedgerCategory, LedgerEntry
from ...db.models.task import Task


@dataclass
class PaymentDisputeResult:
    task_id: UUID
    clawback_cents: int
    bankrupt: bool = False


def handle_payment_dispute(db: Session, event: SimEvent, sim_time) -> PaymentDisputeResult:
    """Deduct clawback amount from company funds and record ledger entry."""
    task_id = UUID(event.payload["task_id"])
    clawback_cents = int(event.payload["clawback_cents"])

    task = db.query(Task).filter(Task.id == task_id).one()
    company_id = task.company_id

    company = db.query(Company).filter(Company.id == company_id).one()
    company.funds_cents -= clawback_cents

    db.add(LedgerEntry(
        company_id=company_id,
        occurred_at=sim_time,
        category=LedgerCategory.PAYMENT_DISPUTE,
        amount_cents=-clawback_cents,
        ref_type="task",
        ref_id=task_id,
    ))

    db.flush()
    bankrupt = company.funds_cents < 0

    return PaymentDisputeResult(
        task_id=task_id,
        clawback_cents=clawback_cents,
        bankrupt=bankrupt,
    )
