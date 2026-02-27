from __future__ import annotations

from decimal import Decimal
from uuid import uuid4
from enum import Enum

from sqlalchemy import BigInteger, CheckConstraint, Enum as SAEnum, ForeignKey, Numeric, String, Uuid
from sqlalchemy.orm import mapped_column

from ..base import Base

class Domain(str, Enum):
    SYSTEM = "system"
    RESEARCH = "research"
    DATA = "data"
    FRONTEND = "frontend"
    BACKEND = "backend"
    TRAINING = "training"
    HARDWARE = "hardware"

class Company(Base):
    __tablename__ = "companies"

    id = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    name = mapped_column(
        String(255),
        nullable=False,
    )
    funds_cents = mapped_column(
        BigInteger,
        nullable=False,
    )
    
class CompanyPrestige(Base):
    __tablename__ = "company_prestige"
    __table_args__ = (
        CheckConstraint("prestige_level >= 1 AND prestige_level <= 10", name="ck_company_prestige_prestige_level_range"),
    )

    company_id = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    domain = mapped_column(
        SAEnum(Domain, name="domain", values_callable=lambda e: [x.value for x in e]),
        primary_key=True,
        nullable=False,
    )
    prestige_level = mapped_column(
        Numeric(6, 3),
        nullable=False,
        default=Decimal("1.000"),
    )

__all__ = ["Domain", "Company", "CompanyPrestige"]
