from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import typer
from sqlalchemy import func

from ..core.business_time import first_business_of_month
from ..db.models.company import Company, CompanyPrestige
from ..db.models.employee import Employee
from ..db.models.sim_state import SimState
from ..db.models.task import Task, TaskStatus
from . import get_db, json_output, error_output

company_app = typer.Typer(help="Company status commands.")


def _next_payroll_date(sim_time: datetime) -> datetime:
    """Compute next first-business-day-of-month at 09:00 from sim_time."""
    if sim_time.month == 12:
        next_month = sim_time.replace(year=sim_time.year + 1, month=1, day=1)
    else:
        next_month = sim_time.replace(month=sim_time.month + 1, day=1)
    return first_business_of_month(next_month)


@company_app.command("status")
def company_status():
    """Show company status: funds, prestige, tasks, payroll, risk."""
    with get_db() as db:
        sim_state = db.query(SimState).first()
        if sim_state is None:
            error_output("No simulation found. Run `yc-bench sim init` first.")

        company = (
            db.query(Company).filter(Company.id == sim_state.company_id).one_or_none()
        )
        if company is None:
            error_output("Company not found.")

        # Prestige by domain
        prestige_rows = (
            db.query(CompanyPrestige)
            .filter(CompanyPrestige.company_id == company.id)
            .all()
        )
        prestige_map = {
            row.domain.value: float(row.prestige_level) for row in prestige_rows
        }

        # Task counts
        active_count = (
            db.query(func.count(Task.id))
            .filter(
                Task.company_id == company.id,
                Task.status == TaskStatus.ACTIVE,
            )
            .scalar()
            or 0
        )

        planned_count = (
            db.query(func.count(Task.id))
            .filter(
                Task.company_id == company.id,
                Task.status == TaskStatus.PLANNED,
            )
            .scalar()
            or 0
        )

        completed_count = (
            db.query(func.count(Task.id))
            .filter(
                Task.company_id == company.id,
                Task.status.in_(
                    [TaskStatus.COMPLETED_SUCCESS, TaskStatus.COMPLETED_FAIL]
                ),
            )
            .scalar()
            or 0
        )

        cancelled_count = (
            db.query(func.count(Task.id))
            .filter(
                Task.company_id == company.id,
                Task.status == TaskStatus.CANCELLED,
            )
            .scalar()
            or 0
        )

        # Employee count
        employee_count = (
            db.query(func.count(Employee.id))
            .filter(Employee.company_id == company.id)
            .scalar()
            or 0
        )

        # Monthly payroll estimate
        total_salary = (
            db.query(func.sum(Employee.salary_cents))
            .filter(Employee.company_id == company.id)
            .scalar()
            or 0
        )

        next_payroll = _next_payroll_date(sim_state.sim_time)

        # Bankruptcy risk
        months_runway = (
            round(float(company.funds_cents) / float(total_salary), 2)
            if total_salary > 0
            else None
        )

        json_output(
            {
                "company_id": str(company.id),
                "company_name": company.name,
                "funds_cents": company.funds_cents,
                "prestige": prestige_map,
                "sim_time": sim_state.sim_time.isoformat(),
                "horizon_end": sim_state.horizon_end.isoformat(),
                "tasks": {
                    "active": active_count,
                    "planned": planned_count,
                    "completed": completed_count,
                    "cancelled": cancelled_count,
                },
                "employees": employee_count,
                "next_payroll": next_payroll.isoformat(),
                "monthly_payroll_cents": total_salary,
                "risk": {
                    "months_runway": months_runway,
                    "bankrupt": company.funds_cents < 0,
                },
            }
        )
