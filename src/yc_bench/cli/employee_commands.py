from __future__ import annotations

import typer
from sqlalchemy import func

from ..db.models.employee import Employee
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
            # Current active assignments
            active_assignments = (
                db.query(TaskAssignment.task_id)
                .join(Task, Task.id == TaskAssignment.task_id)
                .filter(
                    TaskAssignment.employee_id == emp.id,
                    Task.status == TaskStatus.ACTIVE,
                )
                .all()
            )
            active_task_ids = [str(a.task_id) for a in active_assignments]

            results.append({
                "employee_id": str(emp.id),
                "name": emp.name,
                "tier": emp.tier,
                "salary_cents": emp.salary_cents,
                "work_hours_per_day": float(emp.work_hours_per_day),
                "active_task_count": len(active_task_ids),
                "active_task_ids": active_task_ids,
            })

        json_output({
            "count": len(results),
            "employees": results,
        })
