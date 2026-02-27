from __future__ import annotations

from uuid import uuid4

from sqlalchemy import ForeignKey, Text, Uuid
from sqlalchemy.orm import mapped_column

from ..base import Base


class Scratchpad(Base):
    __tablename__ = "scratchpads"

    company_id = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    content = mapped_column(
        Text,
        nullable=False,
        default="",
    )


__all__ = ["Scratchpad"]
