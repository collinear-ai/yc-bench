from __future__ import annotations

from enum import Enum
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Enum as SAEnum, ForeignKey, Integer, Numeric, String, Uuid, text
from sqlalchemy.orm import mapped_column

from ..base import Base
from .company import Domain

class TaskStatus(str, Enum):
    MARKET = "market"
    PLANNED = "planned"
    ACTIVE = "active"
    COMPLETED_SUCCESS = "completed_success"
    COMPLETED_FAIL = "completed_fail"
    CANCELLED = "cancelled"

class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("required_prestige >= 1 AND required_prestige <= 10", name="ck_tasks_required_prestige_range"),
        CheckConstraint("skill_boost_pct >= 0", name="ck_tasks_skill_boost_pct_range"),
        CheckConstraint("reward_funds_cents >= 0", name="ck_tasks_reward_funds_cents_gte_0"),
        CheckConstraint("reward_prestige_delta >= 0 AND reward_prestige_delta <= 5", name="ck_tasks_reward_prestige_delta_range"),
        CheckConstraint("required_trust >= 0 AND required_trust <= 5", name="ck_tasks_required_trust_range"),
    )

    id = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    company_id = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=True,
    )
    client_id = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("clients.id", ondelete="SET NULL"),
        nullable=True,
    )
    status = mapped_column(
        SAEnum(TaskStatus, name="task_status", values_callable=lambda e: [x.value for x in e]),
        nullable=False,
        default=TaskStatus.MARKET,
    )
    title = mapped_column(
        String(255),
        nullable=False,
    )
    required_prestige = mapped_column(
        Integer,
        nullable=False,
    )
    reward_funds_cents = mapped_column(
        BigInteger,
        nullable=False,
    )
    reward_prestige_delta = mapped_column(
        Numeric(6, 3),
        nullable=False,
    )
    skill_boost_pct = mapped_column(
        Numeric(6, 4),
        nullable=False,
    )
    accepted_at = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    deadline = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    success = mapped_column(
        Boolean,
        nullable=True,
    )
    progress_milestone_pct = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    required_trust = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    advertised_reward_cents = mapped_column(
        BigInteger,
        nullable=True,
    )

class TaskRequirement(Base):
    __tablename__ = "task_requirements"
    __table_args__ = (
        CheckConstraint("required_qty >= 200 AND required_qty <= 25000", name="ck_task_requirements_required_qty_range"),
        CheckConstraint("completed_qty >= 0", name="ck_task_requirements_completed_qty_gte_0"),
        CheckConstraint("completed_qty <= required_qty", name="ck_task_requirements_completed_qty_lte_required_qty"),
    )

    task_id = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    domain = mapped_column(
        SAEnum(Domain, name="domain", values_callable=lambda e: [x.value for x in e]),
        primary_key=True,
        nullable=False,
    )
    required_qty = mapped_column(
        Numeric(14, 4),
        nullable=False,
    )
    completed_qty = mapped_column(
        Numeric(14, 4),
        nullable=False,
        default=0,
    )

class TaskAssignment(Base):
    __tablename__ = "task_assignments"

    task_id = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    employee_id = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("employees.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    assigned_at = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

__all__ = ["TaskStatus", "Task", "TaskRequirement", "TaskAssignment"]
