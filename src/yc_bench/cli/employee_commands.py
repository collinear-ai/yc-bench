from __future__ import annotations

import typer
from sqlalchemy import func

from ..db.models.employee import Employee, EmployeeSkillRate
from ..db.models.task import Task, TaskAssignment, TaskStatus
from ..db.models.sim_state import SimState
from . import get_db, json_output, error_output

employee_app = typer.Typer(help="Employee management commands.")


@employee_app.command("list")
def employee_list():
    """List all employees with their skills and current assignments."""
    with get_db() as db:
        sim_state = db.query(SimState).first()
        if sim_state is None:
            error_output("No simulation found.")

        employees = db.query(Employee).filter(
            Employee.company_id == sim_state.company_id
        ).all()

        results = []
        for emp in employees:
            # Current active assignments (show task titles, not UUIDs)
            active_assignments = (
                db.query(Task)
                .join(TaskAssignment, Task.id == TaskAssignment.task_id)
                .filter(
                    TaskAssignment.employee_id == emp.id,
                    Task.status == TaskStatus.ACTIVE,
                )
                .all()
            )
            active_tasks = [t.title for t in active_assignments]

            # Skill rates per domain
            skill_rows = db.query(EmployeeSkillRate).filter(
                EmployeeSkillRate.employee_id == emp.id
            ).all()
            skill_rates = {
                r.domain.value: round(float(r.rate_domain_per_hour), 2)
                for r in skill_rows
            }

            results.append({
                "name": emp.name,
                "tier": emp.tier,
                "salary_cents": emp.salary_cents,
                "skill_rates": skill_rates,
                "active_tasks": active_tasks,
            })

        json_output({
            "count": len(results),
            "employees": results,
        })
