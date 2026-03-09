from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String, Uuid
from sqlalchemy.orm import mapped_column

from ..base import Base


class Client(Base):
    __tablename__ = "clients"

    id = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    name = mapped_column(
        String(255),
        nullable=False,
    )


class ClientTrust(Base):
    __tablename__ = "client_trust"
    __table_args__ = (
        CheckConstraint(
            "trust_level >= 0 AND trust_level <= 5",
            name="ck_client_trust_level_range",
        ),
    )

    company_id = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    client_id = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    trust_level = mapped_column(
        Numeric(6, 3),
        nullable=False,
        default=Decimal("0.000"),
    )


__all__ = ["Client", "ClientTrust"]
