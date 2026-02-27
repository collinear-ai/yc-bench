from __future__ import annotations

from uuid import uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Uuid, Date, Enum as SAEnum
from sqlalchemy.orm import mapped_column

from ..base import Base
from .event import EventType

class Session(Base):
    __tablename__ = "sessions"
    
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
    started_at = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    ended_at = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    wake_reason = mapped_column(
        SAEnum(EventType, name="event_type", values_callable=lambda e: [x.value for x in e]),
        nullable=False,
    )
    
class MonthlyMetric(Base):
    __tablename__ = "monthly_metrics"
    
    company_id = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    month_start = mapped_column(
        Date,
        primary_key=True,
        nullable=False,
    )
    revenue_cents = mapped_column(
        BigInteger,
        nullable=False,
    )
    cost_cents = mapped_column(
        BigInteger,
        nullable=False,
    )
    return_cents = mapped_column(
        BigInteger,
        nullable=False,
    )
    ending_funds_cents = mapped_column(
        BigInteger,
        nullable=False,
    )

__all__ = ["Session", "MonthlyMetric"]