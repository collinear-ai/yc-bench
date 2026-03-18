"""Event infrastructure: fetch, consume, and insert simulation events.

Events are processed in deterministic order: (scheduled_at, priority, id).
Priority by event_type: task_completed=0, bankruptcy=1, task_half=2, horizon_end=3.
"""
from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Dict, Optional
from uuid import UUID
from uuid import uuid5, NAMESPACE_URL

from sqlalchemy import case
from sqlalchemy.orm import Session

from ..db.models.event import EventType, SimEvent

# Priority ordering — lower number = higher priority
EVENT_PRIORITY: Dict[EventType, int] = {
    EventType.TASK_COMPLETED: 0,
    EventType.PAYMENT_DISPUTE: 1,
    EventType.BANKRUPTCY: 2,
    EventType.TASK_HALF_PROGRESS: 3,
    EventType.HORIZON_END: 4,
}


def _deterministic_event_id(
    company_id: UUID,
    event_type: EventType,
    scheduled_at: datetime,
    dedupe_key: Optional[str],
    payload: Dict[str, Any],
) -> UUID:
    """Generate deterministic event UUID to stabilize same-seed replay ordering."""
    payload_key = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    base = "|".join(
        [
            str(company_id),
            event_type.value,
            scheduled_at.isoformat(),
            dedupe_key or "",
            payload_key,
        ]
    )
    return uuid5(NAMESPACE_URL, base)


def fetch_next_event(
    db: Session,
    company_id: UUID,
    up_to: datetime,
) -> Optional[SimEvent]:
    """Fetch the next unconsumed event scheduled at or before up_to.

    Deterministic order: (scheduled_at ASC, priority ASC, id ASC).
    """
    priority_expr = case(
        {et: p for et, p in EVENT_PRIORITY.items()},
        value=SimEvent.event_type,
        else_=99,
    )

    event = (
        db.query(SimEvent)
        .filter(
            SimEvent.company_id == company_id,
            SimEvent.consumed == False,
            SimEvent.scheduled_at <= up_to,
        )
        .order_by(
            SimEvent.scheduled_at.asc(),
            priority_expr.asc(),
            SimEvent.id.asc(),
        )
        .first()
    )

    return event


def consume_event(db: Session, event: SimEvent) -> None:
    """Mark an event as consumed."""
    event.consumed = True
    db.flush()


def insert_event(
    db: Session,
    company_id: UUID,
    event_type: EventType,
    scheduled_at: datetime,
    payload: Dict[str, Any],
    dedupe_key: Optional[str] = None,
) -> SimEvent:
    """Insert a new event, with optional idempotent deduplication.

    If dedupe_key is provided and an unconsumed event with the same key exists,
    the existing event is returned unchanged.
    """
    if dedupe_key is not None:
        existing = db.query(SimEvent).filter(
            SimEvent.company_id == company_id,
            SimEvent.dedupe_key == dedupe_key,
            SimEvent.consumed == False,
        ).first()
        if existing is not None:
            return existing

    event = SimEvent(
        id=_deterministic_event_id(company_id, event_type, scheduled_at, dedupe_key, payload),
        company_id=company_id,
        event_type=event_type,
        scheduled_at=scheduled_at,
        payload=payload,
        dedupe_key=dedupe_key,
    )
    db.add(event)
    db.flush()
    return event


__all__ = [
    "EVENT_PRIORITY",
    "fetch_next_event",
    "consume_event",
    "insert_event",
]
