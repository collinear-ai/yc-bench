from __future__ import annotations

from enum import Enum
from uuid import uuid4

from sqlalchemy import BigInteger, DateTime, Enum as SAEnum, ForeignKey, String, Uuid
from sqlalchemy.orm import mapped_column

from ..base import Base

class LedgerCategory(str, Enum):
    MONTHLY_PAYROLL = "monthly_payroll"
    TASK_REWARD = "task_reward"
    TASK_FAIL_PENALTY = "task_fail_penalty"
    TASK_CANCEL_PENALTY = "task_cancel_penalty"

class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    
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
    occurred_at = mapped_column(  
        DateTime(timezone=True),
        nullable=False,
    )
    category = mapped_column(
        SAEnum(LedgerCategory, name="ledger_category", values_callable=lambda e: [x.value for x in e]),
        nullable=False,
    )
    amount_cents = mapped_column(
        BigInteger,
        nullable=False,
    )
    ref_type = mapped_column(
        String(64),
        nullable=True,
    )
    ref_id = mapped_column( # can refer multiple tables, no foreign key
        Uuid(as_uuid=True),
        nullable=True,
    )

__all__ = ["LedgerCategory", "LedgerEntry"]