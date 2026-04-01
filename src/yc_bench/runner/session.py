"""Session utilities for the benchmark runner."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from ..db.models.event import EventType
from ..db.models.session import Session as SessionModel


def open_session(db: Session, company_id, wake_reason: EventType) -> SessionModel:
    """Create and persist a new session record."""
    session = SessionModel(
        id=uuid4(),
        company_id=company_id,
        started_at=datetime.now(timezone.utc),
        ended_at=None,
        wake_reason=wake_reason,
    )
    db.add(session)
    db.flush()
    return session


def close_session(db: Session, session: SessionModel) -> None:
    """Close an open session record."""
    session.ended_at = datetime.now(timezone.utc)
    db.flush()


__all__ = ["open_session", "close_session"]
