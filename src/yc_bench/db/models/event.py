from __future__ import annotations

from enum import Enum
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, ForeignKey, JSON, String, Uuid, text
from sqlalchemy.orm import mapped_column

from ..base import Base

class EventType(str, Enum):
    TASK_HALF_PROGRESS = "task_half_progress"
    TASK_COMPLETED = "task_completed"
    BANKRUPTCY = "bankruptcy"
    HORIZON_END = "horizon_end"

class SimEvent(Base):
    __tablename__ = "sim_events"
    
    id = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    company_id = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type = mapped_column(
        SAEnum(EventType, name="event_type", values_callable=lambda e: [x.value for x in e]),
        nullable=False,
    )
    scheduled_at = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    payload = mapped_column(
        JSON,
        nullable=False,
    )
    dedupe_key = mapped_column( # guardrail against duplicates in event recomputation
        String(255),
        nullable=True,
    )
    consumed = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )

__all__ = ["EventType", "SimEvent"]
