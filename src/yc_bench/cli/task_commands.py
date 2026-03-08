from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

import typer
from sqlalchemy import func

from ..core.business_time import add_business_hours
from ..core.eta import recalculate_etas
from ..db.models.company import Company, CompanyPrestige
from ..db.models.employee import Employee
from ..db.models.event import SimEvent
from ..db.models.sim_state import SimState
from ..db.models.task import Task, TaskAssignment, TaskRequirement, TaskStatus
from ..services.generate_tasks import generate_replacement_task
from . import get_db, json_output, error_output


def _get_world_cfg():
    """Load WorldConfig from the active experiment (YC_BENCH_EXPERIMENT env var)."""
    from yc_bench.config import get_world_config
    return get_world_config()

task_app = typer.Typer(help="Task management commands.")


def _compute_deadline(accepted_at: datetime, max_domain_qty: float, cfg=None) -> datetime:
    """Deadline based on the heaviest single domain (domains are worked in parallel)."""
    if cfg is None:
        cfg = _get_world_cfg()
    work_hours = cfg.workday_end_hour - cfg.workday_start_hour
    biz_days = max(cfg.deadline_min_biz_days, int(max_domain_qty / cfg.deadline_qty_per_day))
    return add_business_hours(accepted_at, Decimal(str(biz_days)) * Decimal(str(work_hours)))


@task_app.command("accept")
def task_accept(
    task_id: str = typer.Option(..., "--task-id", help="UUID of the task to accept"),
):
    """Accept a market task: transition to planned, assign to company, generate replacement."""
    try:
        tid = UUID(task_id)
    except ValueError:
        error_output(f"Invalid UUID: {task_id}")

    with get_db() as db:
        sim_state = db.query(SimState).first()
        if sim_state is None:
            error_output("No simulation found. Run `yc-bench sim init` first.")

        task = db.query(Task).filter(Task.id == tid).one_or_none()
        if task is None:
            error_output(f"Task {task_id} not found.")
        if task.status != TaskStatus.MARKET:
            error_output(f"Task {task_id} is not in market status (current: {task.status.value}).")

        # Validate per-domain prestige requirement
        company_id = sim_state.company_id
        reqs = db.query(TaskRequirement).filter(TaskRequirement.task_id == tid).all()
        prestige_rows = db.query(CompanyPrestige).filter(
            CompanyPrestige.company_id == company_id
        ).all()
        prestige_map = {p.domain: float(p.prestige_level) for p in prestige_rows}

        for req in reqs:
            domain_prestige = prestige_map.get(req.domain, 1.0)
            if task.required_prestige > domain_prestige:
                error_output(
                    f"Company prestige in {req.domain.value} ({domain_prestige:.1f}) "
                    f"does not meet task requirement ({task.required_prestige})."
                )
        max_domain_qty = max(float(r.required_qty) for r in reqs)
        accepted_at = sim_state.sim_time
        deadline = _compute_deadline(accepted_at, max_domain_qty)

        # Transition task
        task.status = TaskStatus.PLANNED
        task.company_id = company_id
        task.accepted_at = accepted_at
        task.deadline = deadline

        # Generate replacement task
        counter = sim_state.replenish_counter
        sim_state.replenish_counter = counter + 1
        replacement = generate_replacement_task(
            run_seed=sim_state.run_seed,
            replenish_counter=counter,
            replaced_prestige=task.required_prestige,
            cfg=_get_world_cfg(),
        )

        replacement_row = Task(
            id=uuid4(),
            company_id=None,
            status=TaskStatus.MARKET,
            title=replacement.title,
            required_prestige=replacement.required_prestige,
            reward_funds_cents=replacement.reward_funds_cents,
            reward_prestige_delta=replacement.reward_prestige_delta,
            skill_boost_pct=replacement.skill_boost_pct,
            accepted_at=None,
            deadline=None,
            completed_at=None,
            success=None,
            progress_milestone_pct=0,
        )
        db.add(replacement_row)

        for domain, qty in replacement.requirements.items():
            db.add(TaskRequirement(
                task_id=replacement_row.id,
                domain=domain,
                required_qty=qty,
                completed_qty=0,
            ))

        db.flush()

        json_output({
            "task_id": str(task.id),
            "status": task.status.value,
            "accepted_at": accepted_at.isoformat(),
            "deadline": deadline.isoformat(),
            "replacement_task_id": str(replacement_row.id),
        })


@task_app.command("assign")
def task_assign(
    task_id: str = typer.Option(..., "--task-id", help="UUID of the task"),
    employee_id: str = typer.Option(..., "--employee-id", help="UUID of the employee"),
):
    """Assign an employee to a task."""
    try:
        tid = UUID(task_id)
        eid = UUID(employee_id)
    except ValueError:
        error_output("Invalid UUID provided.")

    with get_db() as db:
        sim_state = db.query(SimState).first()
        if sim_state is None:
            error_output("No simulation found.")

        task = db.query(Task).filter(Task.id == tid).one_or_none()
        if task is None:
            error_output(f"Task {task_id} not found.")
        if task.status not in (TaskStatus.PLANNED, TaskStatus.ACTIVE):
            error_output(f"Task {task_id} must be planned or active to assign (current: {task.status.value}).")
        if task.company_id != sim_state.company_id:
            error_output(f"Task {task_id} does not belong to your company.")

        employee = db.query(Employee).filter(Employee.id == eid).one_or_none()
        if employee is None:
            error_output(f"Employee {employee_id} not found.")
        if employee.company_id != sim_state.company_id:
            error_output(f"Employee {employee_id} does not belong to your company.")

        # Check if already assigned
        existing = db.query(TaskAssignment).filter(
            TaskAssignment.task_id == tid,
            TaskAssignment.employee_id == eid,
        ).one_or_none()
        if existing is not None:
            error_output(f"Employee {employee_id} is already assigned to task {task_id}.")

        assignment = TaskAssignment(
            task_id=tid,
            employee_id=eid,
            assigned_at=sim_state.sim_time,
        )
        db.add(assignment)
        db.flush()

        # Recalculate ETAs for all active tasks sharing this employee
        if task.status == TaskStatus.ACTIVE:
            emp_assignments = db.query(TaskAssignment).filter(
                TaskAssignment.employee_id == eid
            ).all()
            impacted = set()
            for ea in emp_assignments:
                t = db.query(Task).filter(Task.id == ea.task_id).one_or_none()
                if t and t.status == TaskStatus.ACTIVE:
                    impacted.add(t.id)
            if impacted:
                recalculate_etas(db, sim_state.company_id, sim_state.sim_time, impacted, milestones=_get_world_cfg().task_progress_milestones)

        # Return current assignment list
        assignments = db.query(TaskAssignment).filter(TaskAssignment.task_id == tid).all()
        assignment_list = [
            {
                "employee_id": str(a.employee_id),
                "assigned_at": a.assigned_at.isoformat(),
            }
            for a in assignments
        ]

        json_output({
            "task_id": str(task.id),
            "status": task.status.value,
            "assignments": assignment_list,
        })


@task_app.command("dispatch")
def task_dispatch(
    task_id: str = typer.Option(..., "--task-id", help="UUID of the task to dispatch"),
):
    """Dispatch a planned task to active status."""
    try:
        tid = UUID(task_id)
    except ValueError:
        error_output(f"Invalid UUID: {task_id}")

    with get_db() as db:
        sim_state = db.query(SimState).first()
        if sim_state is None:
            error_output("No simulation found.")

        task = db.query(Task).filter(Task.id == tid).one_or_none()
        if task is None:
            error_output(f"Task {task_id} not found.")
        if task.status != TaskStatus.PLANNED:
            error_output(f"Task {task_id} must be planned to dispatch (current: {task.status.value}).")
        if task.company_id != sim_state.company_id:
            error_output(f"Task {task_id} does not belong to your company.")

        # Validate at least one assignment
        assignment_count = db.query(func.count(TaskAssignment.employee_id)).filter(
            TaskAssignment.task_id == tid
        ).scalar() or 0
        if assignment_count == 0:
            error_output(f"Task {task_id} has no assignments. Assign employees before dispatching.")

        # Transition to active
        task.status = TaskStatus.ACTIVE
        db.flush()

        # Recalculate ETAs for this task and other active tasks that share assigned employees.
        impacted = {tid}
        assigned = db.query(TaskAssignment).filter(TaskAssignment.task_id == tid).all()
        for a in assigned:
            peer_assignments = db.query(TaskAssignment).filter(
                TaskAssignment.employee_id == a.employee_id
            ).all()
            for pa in peer_assignments:
                if pa.task_id == tid:
                    continue
                peer_task = db.query(Task).filter(Task.id == pa.task_id).one_or_none()
                if peer_task and peer_task.status == TaskStatus.ACTIVE:
                    impacted.add(peer_task.id)
        recalculate_etas(db, sim_state.company_id, sim_state.sim_time, impacted, milestones=_get_world_cfg().task_progress_milestones)

        json_output({
            "task_id": str(task.id),
            "status": task.status.value,
            "assignment_count": assignment_count,
        })


@task_app.command("list")
def task_list(
    status: Optional[str] = typer.Option(None, "--status", help="Filter by task status"),
):
    """List tasks owned by the company."""
    with get_db() as db:
        sim_state = db.query(SimState).first()
        if sim_state is None:
            error_output("No simulation found.")

        query = db.query(Task).filter(Task.company_id == sim_state.company_id)

        if status is not None:
            try:
                ts = TaskStatus(status)
            except ValueError:
                error_output(f"Invalid status: {status}. Valid: {[s.value for s in TaskStatus]}")
            query = query.filter(Task.status == ts)

        tasks = query.order_by(Task.accepted_at.desc().nulls_last()).all()
        results = []
        for task in tasks:
            # Compute progress %
            reqs = db.query(TaskRequirement).filter(TaskRequirement.task_id == task.id).all()
            total_required = sum(float(r.required_qty) for r in reqs)
            total_completed = sum(float(r.completed_qty) for r in reqs)
            progress_pct = (total_completed / total_required * 100) if total_required > 0 else 0.0

            # Deadline risk
            at_risk = False
            if task.deadline and task.status == TaskStatus.ACTIVE:
                if sim_state.sim_time > task.deadline:
                    at_risk = True

            results.append({
                "task_id": str(task.id),
                "title": task.title,
                "status": task.status.value,
                "progress_pct": round(progress_pct, 2),
                "deadline": task.deadline.isoformat() if task.deadline else None,
                "at_risk": at_risk,
            })

        json_output({
            "count": len(results),
            "tasks": results,
        })


@task_app.command("inspect")
def task_inspect(
    task_id: str = typer.Option(..., "--task-id", help="UUID of the task to inspect"),
):
    """Inspect detailed task information."""
    try:
        tid = UUID(task_id)
    except ValueError:
        error_output(f"Invalid UUID: {task_id}")

    with get_db() as db:
        task = db.query(Task).filter(Task.id == tid).one_or_none()
        if task is None:
            error_output(f"Task {task_id} not found.")

        # Requirements
        reqs = db.query(TaskRequirement).filter(TaskRequirement.task_id == tid).all()
        requirements = []
        for r in reqs:
            requirements.append({
                "domain": r.domain.value,
                "required_qty": float(r.required_qty),
                "completed_qty": float(r.completed_qty),
                "remaining_qty": float(r.required_qty - r.completed_qty),
            })

        # Assignments with employee info
        assignments_raw = db.query(TaskAssignment).filter(TaskAssignment.task_id == tid).all()
        assignments = []
        for a in assignments_raw:
            emp = db.query(Employee).filter(Employee.id == a.employee_id).one_or_none()
            assignments.append({
                "employee_id": str(a.employee_id),
                "employee_name": emp.name if emp else "unknown",
                "assigned_at": a.assigned_at.isoformat(),
            })

        total_required = sum(float(r.required_qty) for r in reqs)
        total_completed = sum(float(r.completed_qty) for r in reqs)
        progress_pct = (total_completed / total_required * 100) if total_required > 0 else 0.0

        json_output({
            "task_id": str(task.id),
            "title": task.title,
            "status": task.status.value,
            "required_prestige": task.required_prestige,
            "reward_funds_cents": task.reward_funds_cents,
            "reward_prestige_delta": float(task.reward_prestige_delta),
            "skill_boost_pct": float(task.skill_boost_pct),
            "accepted_at": task.accepted_at.isoformat() if task.accepted_at else None,
            "deadline": task.deadline.isoformat() if task.deadline else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "success": task.success,
            "progress_pct": round(progress_pct, 2),
            "requirements": requirements,
            "assignments": assignments,
        })


@task_app.command("cancel")
def task_cancel(
    task_id: str = typer.Option(..., "--task-id", help="UUID of the task to cancel"),
    reason: str = typer.Option(..., "--reason", help="Reason for cancellation"),
):
    """Cancel a task and apply prestige penalty."""
    try:
        tid = UUID(task_id)
    except ValueError:
        error_output(f"Invalid UUID: {task_id}")

    with get_db() as db:
        sim_state = db.query(SimState).first()
        if sim_state is None:
            error_output("No simulation found.")

        task = db.query(Task).filter(Task.id == tid).one_or_none()
        if task is None:
            error_output(f"Task {task_id} not found.")
        if task.status not in (TaskStatus.PLANNED, TaskStatus.ACTIVE):
            error_output(f"Task {task_id} cannot be cancelled (current: {task.status.value}).")
        if task.company_id != sim_state.company_id:
            error_output(f"Task {task_id} does not belong to your company.")

        # Apply prestige penalty: penalty_cancel_multiplier * reward_prestige_delta across task's required domains
        _cfg = _get_world_cfg()
        cancel_penalty = Decimal(str(_cfg.penalty_cancel_multiplier)) * task.reward_prestige_delta
        reqs = db.query(TaskRequirement).filter(TaskRequirement.task_id == tid).all()
        penalties_applied = {}

        for req in reqs:
            prestige = db.query(CompanyPrestige).filter(
                CompanyPrestige.company_id == sim_state.company_id,
                CompanyPrestige.domain == req.domain,
            ).one_or_none()
            if prestige is not None:
                old_val = prestige.prestige_level
                new_val = max(Decimal(str(_cfg.prestige_min)), prestige.prestige_level - cancel_penalty)
                prestige.prestige_level = new_val
                penalties_applied[req.domain.value] = {
                    "old": float(old_val),
                    "new": float(new_val),
                    "delta": float(old_val - new_val),
                }

        # Set status to cancelled
        task.status = TaskStatus.CANCELLED

        # Drop pending events for this task
        pending_events = db.query(SimEvent).filter(
            SimEvent.company_id == sim_state.company_id,
            SimEvent.consumed == False,
            SimEvent.payload["task_id"].astext == str(tid),
        ).all()
        for ev in pending_events:
            ev.consumed = True

        # Recalculate ETAs for tasks sharing freed employees
        cancelled_assignments = db.query(TaskAssignment).filter(
            TaskAssignment.task_id == tid
        ).all()
        freed_emp_ids = {a.employee_id for a in cancelled_assignments}
        impacted = set()
        for emp_id in freed_emp_ids:
            emp_assignments = db.query(TaskAssignment).filter(
                TaskAssignment.employee_id == emp_id
            ).all()
            for ea in emp_assignments:
                if ea.task_id != tid:
                    t = db.query(Task).filter(Task.id == ea.task_id).one_or_none()
                    if t and t.status == TaskStatus.ACTIVE:
                        impacted.add(t.id)
        if impacted:
            recalculate_etas(db, sim_state.company_id, sim_state.sim_time, impacted, milestones=_get_world_cfg().task_progress_milestones)

        # Bankruptcy check
        company = db.query(Company).filter(Company.id == sim_state.company_id).one()
        bankrupt = company.funds_cents < 0

        db.flush()

        json_output({
            "task_id": str(task.id),
            "status": task.status.value,
            "reason": reason,
            "cancel_penalty_per_domain": float(cancel_penalty),
            "prestige_changes": penalties_applied,
            "bankrupt": bankrupt,
        })
