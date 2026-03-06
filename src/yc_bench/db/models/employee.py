from __future__ import annotations

from uuid import uuid4

from decimal import Decimal
from sqlalchemy import BigInteger, CheckConstraint, Enum as SAEnum, ForeignKey, Numeric, String, Uuid
from sqlalchemy.orm import mapped_column

from ..base import Base
from .company import Domain

class Employee(Base):
    __tablename__ = "employees"
    __table_args__ = (
        CheckConstraint("work_hours_per_day > 0", name="ck_employees_work_hours_per_day_gt_0"),
        CheckConstraint("salary_cents >= 0", name="ck_employees_salary_cents_gte_0"),
    )

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
    name = mapped_column(
        String(255),
        nullable=False,
    )
    tier = mapped_column(
        String(20),
        nullable=False,
        default="junior",
    )
    work_hours_per_day = mapped_column(
        Numeric(5, 2),
        nullable=False,
        default=Decimal("9.00"),
    )
    salary_cents = mapped_column(
        BigInteger,
        nullable=False,
    )

class EmployeeSkillRate(Base):
    __tablename__ = "employee_skill_rates"
    __table_args__ = (
        CheckConstraint("rate_domain_per_hour >= 0", name="ck_employee_skill_rates_rate_domain_per_hour_gte_0"),
    )

    employee_id = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("employees.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    domain = mapped_column(
        SAEnum(Domain, name="domain", values_callable=lambda e: [x.value for x in e]),
        primary_key=True,
        nullable=False,
    )
    rate_domain_per_hour = mapped_column(
        Numeric(12, 4),
        nullable=False,
        default=Decimal("1.0000"),
    )

__all__ = ["Employee", "EmployeeSkillRate"]
