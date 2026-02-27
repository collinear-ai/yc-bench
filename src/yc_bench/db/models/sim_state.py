from __future__ import annotations

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Uuid
from sqlalchemy.orm import mapped_column

from ..base import Base


class SimState(Base):
    __tablename__ = "sim_state"

    company_id = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    sim_time = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    run_seed = mapped_column(
        Integer,
        nullable=False,
    )
    horizon_end = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    replenish_counter = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

__all__ = ["SimState"]
