"""Handler for task_completed events.

On completion:
- If completion_time <= deadline: success → add reward funds, add prestige, skill-boost employees.
- If completion_time > deadline: fail → set completed_fail, apply 0.8 * delta prestige penalty.
After either outcome, recalculate ETAs (freed employees change topology).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict
from uuid import UUID

from sqlalchemy.orm import Session

from ...db.models.company import Company, CompanyPrestige, Domain
from ...db.models.employee import Employee, EmployeeSkillRate
from ...config import get_world_config
from ...db.models.event import SimEvent
from ...db.models.ledger import LedgerCategory, LedgerEntry
from ...db.models.task import Task, TaskAssignment, TaskRequirement, TaskStatus


@dataclass
class TaskCompleteResult:
    task_id: UUID
    success: bool
    funds_delta: int = 0
    prestige_changes: Dict[str, float] = field(default_factory=dict)
    bankrupt: bool = False


def handle_task_complete(db: Session, event: SimEvent, sim_time) -> TaskCompleteResult:
    """Process task completion: finalize progress, determine success/fail, apply rewards/penalties."""
    task_id = UUID(event.payload["task_id"])
    task = db.query(Task).filter(Task.id == task_id).one()
    company_id = task.company_id

    # Finalize all domain progress to 100%
    reqs = db.query(TaskRequirement).filter(TaskRequirement.task_id == task_id).all()
    for req in reqs:
        req.completed_qty = req.required_qty
    db.flush()

    task.completed_at = sim_time
    success = sim_time <= task.deadline

    wc = get_world_config()
    prestige_changes: Dict[str, float] = {}
    funds_delta = 0

    if success:
        task.status = TaskStatus.COMPLETED_SUCCESS
        task.success = True

        # Add reward funds
        company = db.query(Company).filter(Company.id == company_id).one()
        company.funds_cents += task.reward_funds_cents
        funds_delta = task.reward_funds_cents

        # Ledger entry
        db.add(LedgerEntry(
            company_id=company_id,
            occurred_at=sim_time,
            category=LedgerCategory.TASK_REWARD,
            amount_cents=task.reward_funds_cents,
            ref_type="task",
            ref_id=task_id,
        ))

        # Add prestige to each domain
        for req in reqs:
            prestige = db.query(CompanyPrestige).filter(
                CompanyPrestige.company_id == company_id,
                CompanyPrestige.domain == req.domain,
            ).one_or_none()
            if prestige is not None:
                old = float(prestige.prestige_level)
                prestige.prestige_level = min(
                    Decimal(str(wc.prestige_max)),
                    prestige.prestige_level + task.reward_prestige_delta,
                )
                prestige_changes[req.domain.value] = float(prestige.prestige_level) - old

        # Skill boost assigned employees
        assignments = db.query(TaskAssignment).filter(
            TaskAssignment.task_id == task_id
        ).all()
        if task.skill_boost_pct > 0:
            task_domains = {req.domain for req in reqs}
            for a in assignments:
                for domain in task_domains:
                    skill = db.query(EmployeeSkillRate).filter(
                        EmployeeSkillRate.employee_id == a.employee_id,
                        EmployeeSkillRate.domain == domain,
                    ).one_or_none()
                    if skill is not None:
                        skill.rate_domain_per_hour = min(
                            skill.rate_domain_per_hour + task.skill_boost_pct,
                            Decimal("10"),
                        )

        # Salary bump: small raise for each employee who contributed to this task
        if wc.salary_bump_pct > 0:
            for a in assignments:
                employee = db.query(Employee).filter(Employee.id == a.employee_id).one_or_none()
                if employee is not None:
                    bump = int(employee.salary_cents * wc.salary_bump_pct)
                    employee.salary_cents += bump

    else:
        task.status = TaskStatus.COMPLETED_FAIL
        task.success = False

        # Apply penalty_fail_multiplier * reward_prestige_delta penalty
        penalty = Decimal(str(wc.penalty_fail_multiplier)) * task.reward_prestige_delta
        for req in reqs:
            prestige = db.query(CompanyPrestige).filter(
                CompanyPrestige.company_id == company_id,
                CompanyPrestige.domain == req.domain,
            ).one_or_none()
            if prestige is not None:
                old = float(prestige.prestige_level)
                prestige.prestige_level = max(
                    Decimal(str(wc.prestige_min)),
                    prestige.prestige_level - penalty,
                )
                prestige_changes[req.domain.value] = float(prestige.prestige_level) - old

    db.flush()

    # Check bankruptcy
    company = db.query(Company).filter(Company.id == company_id).one()
    bankrupt = company.funds_cents < 0

    return TaskCompleteResult(
        task_id=task_id,
        success=success,
        funds_delta=funds_delta,
        prestige_changes=prestige_changes,
        bankrupt=bankrupt,
    )
