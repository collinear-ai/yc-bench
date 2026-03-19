from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

import typer
from sqlalchemy import func

from ..core.business_time import add_business_hours
from ..core.eta import recalculate_etas
from ..db.models.client import Client, ClientTrust
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
    task_id: str = typer.Option(..., "--task-id", help="Task UUID or title (e.g. Task-42)"),
):
    """Accept a market task: transition to planned, assign to company, generate replacement."""
    with get_db() as db:
        sim_state = db.query(SimState).first()
        if sim_state is None:
            error_output("No simulation found. Run `yc-bench sim init` first.")

        task = _resolve_task(db, task_id)
        if task is None:
            error_output(f"Task '{task_id}' not found.")
        tid = task.id
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
        # Validate client trust requirement
        trust_level = 0.0
        if task.client_id is not None:
            ct = db.query(ClientTrust).filter(
                ClientTrust.company_id == company_id,
                ClientTrust.client_id == task.client_id,
            ).one_or_none()
            if ct is not None:
                trust_level = float(ct.trust_level)
            if task.required_trust > 0 and trust_level < task.required_trust:
                client = db.query(Client).filter(Client.id == task.client_id).one_or_none()
                client_name = client.name if client else "unknown"
                error_output(
                    f"Client trust with {client_name} ({trust_level:.1f}) "
                    f"does not meet task requirement ({task.required_trust})."
                )

        # Apply trust work reduction at accept time (no reward multiplier —
        # faster completion from trust already increases revenue via throughput).
        _cfg = _get_world_cfg()
        if task.client_id is not None:
            work_reduction = _cfg.trust_work_reduction_max * (trust_level / _cfg.trust_max)
            for r in reqs:
                r.required_qty = int(float(r.required_qty) * (1 - work_reduction))

        # Compute deadline from advertised qty BEFORE scope creep
        max_domain_qty = max(float(r.required_qty) for r in reqs)
        accepted_at = sim_state.sim_time
        deadline = _compute_deadline(accepted_at, max_domain_qty)

        # Store advertised reward before any dispute can alter it
        task.advertised_reward_cents = task.reward_funds_cents

        # Scope creep: RAT clients inflate required_qty after accept.
        # Minimum inflation ensures ALL RAT tasks exceed deadline (which was
        # computed from pre-creep qty). The agent can't tell from the deadline
        # alone — the trap only springs after accept.
        if task.client_id is not None:
            client_row = db.query(Client).filter(Client.id == task.client_id).one_or_none()
            if client_row and client_row.loyalty < -0.3:
                intensity = abs(client_row.loyalty)
                inflation = _cfg.scope_creep_max * intensity
                # Ensure enough inflation to bust the deadline:
                # deadline_hours = deadline_min_biz_days * work_hours
                # need inflated_qty / effective_rate > deadline_hours
                # Conservative: at least 130% inflation so even small tasks fail
                inflation = max(1.3, inflation)
                for r in reqs:
                    inflated = float(r.required_qty) * (1 + inflation)
                    r.required_qty = int(min(25000, max(200, inflated)))

        # Transition task
        task.status = TaskStatus.PLANNED
        task.company_id = company_id
        task.accepted_at = accepted_at
        task.deadline = deadline

        # Generate replacement task (inherits same client for stable market distribution)
        counter = sim_state.replenish_counter
        sim_state.replenish_counter = counter + 1

        # Find the client index for the accepted task
        replaced_client_index = 0
        if task.client_id is not None:
            clients = db.query(Client).order_by(Client.name).all()
            for i, c in enumerate(clients):
                if c.id == task.client_id:
                    replaced_client_index = i
                    break

        # Get specialty domains for the replacement client
        replacement_spec_domains = None
        if task.client_id is not None:
            orig_client = db.query(Client).filter(Client.id == task.client_id).one_or_none()
            if orig_client:
                replacement_spec_domains = orig_client.specialty_domains

        replacement = generate_replacement_task(
            run_seed=sim_state.run_seed,
            replenish_counter=counter,
            replaced_prestige=task.required_prestige,
            replaced_client_index=replaced_client_index,
            cfg=_get_world_cfg(),
            specialty_domains=replacement_spec_domains,
        )

        # Look up the actual client for the replacement
        clients = db.query(Client).order_by(Client.name).all()
        replacement_client = clients[replacement.client_index % len(clients)] if clients else None
        replacement_client_id = replacement_client.id if replacement_client else None

        replacement_row = Task(
            id=uuid4(),
            company_id=None,
            client_id=replacement_client_id,
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
            required_trust=replacement.required_trust,
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
            "task_id": task.title,
            "status": task.status.value,
            "accepted_at": accepted_at.isoformat(),
            "deadline": deadline.isoformat(),
            "replacement_task_id": replacement_row.title,
        })


def _resolve_employee(db, company_id, identifier: str):
    """Resolve employee by UUID or name (e.g. 'Emp_1')."""
    try:
        eid = UUID(identifier)
        return db.query(Employee).filter(Employee.id == eid, Employee.company_id == company_id).one_or_none()
    except ValueError:
        pass
    # Try name lookup
    return db.query(Employee).filter(Employee.name == identifier, Employee.company_id == company_id).one_or_none()


def _resolve_task(db, identifier: str):
    """Resolve task by UUID or title (e.g. 'Task-42').

    If multiple tasks share the same title (original + replacement), prefer
    the one that's actionable (market/planned/active) over completed ones.
    """
    try:
        tid = UUID(identifier)
        return db.query(Task).filter(Task.id == tid).one_or_none()
    except ValueError:
        pass
    # Title lookup — prefer actionable tasks over completed ones
    matches = db.query(Task).filter(Task.title == identifier).all()
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    # Prefer: market > planned > active > completed
    priority = {TaskStatus.MARKET: 0, TaskStatus.PLANNED: 1, TaskStatus.ACTIVE: 2}
    matches.sort(key=lambda t: priority.get(t.status, 9))
    return matches[0]


@task_app.command("assign")
def task_assign(
    task_id: str = typer.Option(..., "--task-id", help="Task UUID or title (e.g. Task-42)"),
    employees: str = typer.Option(..., "--employees", help="Comma-separated employee names (e.g. Emp_1,Emp_4,Emp_7)"),
):
    """Assign one or more employees to a task."""
    employee_id = [e.strip() for e in employees.split(",") if e.strip()]
    with get_db() as db:
        sim_state = db.query(SimState).first()
        if sim_state is None:
            error_output("No simulation found.")

        task = _resolve_task(db, task_id)
        if task is None:
            error_output(f"Task '{task_id}' not found.")
        tid = task.id
        if task.status not in (TaskStatus.PLANNED, TaskStatus.ACTIVE):
            error_output(f"Task {task_id} must be planned or active to assign (current: {task.status.value}).")
        if task.company_id != sim_state.company_id:
            error_output(f"Task {task_id} does not belong to your company.")

        assigned_names = []
        for eid_str in employee_id:
            employee = _resolve_employee(db, sim_state.company_id, eid_str)
            if employee is None:
                error_output(f"Employee '{eid_str}' not found.")
            eid = employee.id

            # Skip if already assigned
            existing = db.query(TaskAssignment).filter(
                TaskAssignment.task_id == tid,
                TaskAssignment.employee_id == eid,
            ).one_or_none()
            if existing is not None:
                continue

            db.add(TaskAssignment(
                task_id=tid,
                employee_id=eid,
                assigned_at=sim_state.sim_time,
            ))
            assigned_names.append(employee.name)

        db.flush()

        # Recalculate ETAs for all active tasks sharing these employees
        if task.status == TaskStatus.ACTIVE:
            impacted = set()
            for eid_str in employee_id:
                emp = _resolve_employee(db, sim_state.company_id, eid_str)
                if emp:
                    for ea in db.query(TaskAssignment).filter(TaskAssignment.employee_id == emp.id).all():
                        t = db.query(Task).filter(Task.id == ea.task_id).one_or_none()
                        if t and t.status == TaskStatus.ACTIVE:
                            impacted.add(t.id)
            if impacted:
                recalculate_etas(db, sim_state.company_id, sim_state.sim_time, impacted, milestones=_get_world_cfg().task_progress_milestones)

        # Return current assignment list
        assignments = db.query(TaskAssignment).filter(TaskAssignment.task_id == tid).all()
        assignment_list = []
        for a in assignments:
            emp = db.query(Employee).filter(Employee.id == a.employee_id).one_or_none()
            assignment_list.append(emp.name if emp else "unknown")

        json_output({
            "task_id": task.title,
            "status": task.status.value,
            "newly_assigned": assigned_names,
            "total_assigned": assignment_list,
        })


@task_app.command("assign-all")
def task_assign_all(
    task_id: str = typer.Option(..., "--task-id", help="Task UUID or title (e.g. Task-42)"),
):
    """Disabled — use `task assign --employees Emp_1,Emp_4,Emp_7` to pick specific employees."""
    error_output(
        "assign-all is not available. Use `task assign --task-id <ID> --employees Emp_1,Emp_4,Emp_7` to assign specific employees."
    )


@task_app.command("dispatch")
def task_dispatch(
    task_id: str = typer.Option(..., "--task-id", help="Task UUID or title (e.g. Task-42)"),
):
    """Dispatch a planned task to active status."""
    with get_db() as db:
        sim_state = db.query(SimState).first()
        if sim_state is None:
            error_output("No simulation found.")

        task = _resolve_task(db, task_id)
        if task is None:
            error_output(f"Task '{task_id}' not found.")
        tid = task.id
        if task.status != TaskStatus.PLANNED:
            error_output(f"Task {task_id} must be planned to dispatch (current: {task.status.value}).")
        if task.company_id != sim_state.company_id:
            error_output(f"Task {task_id} does not belong to your company.")

        # Require explicit assignment before dispatch
        existing_count = db.query(func.count(TaskAssignment.employee_id)).filter(
            TaskAssignment.task_id == tid
        ).scalar() or 0
        if existing_count == 0:
            error_output(
                "No employees assigned. Use `task assign-all` or `task assign --employee-id Emp_1` first."
            )
            db.flush()

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

        assigned = db.query(TaskAssignment).filter(TaskAssignment.task_id == tid).all()
        assigned_names = []
        for a in assigned:
            emp = db.query(Employee).filter(Employee.id == a.employee_id).one_or_none()
            if emp:
                assigned_names.append(emp.name)
        json_output({
            "task_id": task.title,
            "status": task.status.value,
            "assignment_count": len(assigned),
            "assigned_employees": assigned_names,
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

            # Look up client name
            client_name = None
            if task.client_id is not None:
                client = db.query(Client).filter(Client.id == task.client_id).one_or_none()
                if client:
                    client_name = client.name

            results.append({
                "task_id": task.title,
                "title": task.title,
                "status": task.status.value,
                "client_name": client_name,
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
    task_id: str = typer.Option(..., "--task-id", help="Task UUID or title (e.g. Task-42)"),
):
    """Inspect detailed task information."""
    with get_db() as db:
        task = _resolve_task(db, task_id)
        if task is None:
            error_output(f"Task '{task_id}' not found.")
        tid = task.id

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
                "employee": emp.name if emp else "unknown",
                "assigned_at": a.assigned_at.isoformat(),
            })

        total_required = sum(float(r.required_qty) for r in reqs)
        total_completed = sum(float(r.completed_qty) for r in reqs)
        progress_pct = (total_completed / total_required * 100) if total_required > 0 else 0.0

        # Look up client name
        client_name = None
        if task.client_id is not None:
            client_row = db.query(Client).filter(Client.id == task.client_id).one_or_none()
            if client_row:
                client_name = client_row.name

        json_output({
            "task_id": task.title,
            "title": task.title,
            "status": task.status.value,
            "client_name": client_name,
            "required_prestige": task.required_prestige,
            "required_trust": task.required_trust,
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
    task_id: str = typer.Option(..., "--task-id", help="Task UUID or title (e.g. Task-42)"),
    reason: str = typer.Option(..., "--reason", help="Reason for cancellation"),
):
    """Cancel a task and apply prestige penalty."""
    with get_db() as db:
        sim_state = db.query(SimState).first()
        if sim_state is None:
            error_output("No simulation found.")

        task = _resolve_task(db, task_id)
        if task is None:
            error_output(f"Task '{task_id}' not found.")
        tid = task.id
        if task.status not in (TaskStatus.PLANNED, TaskStatus.ACTIVE):
            error_output(f"Task {task_id} cannot be cancelled (current: {task.status.value}).")
        if task.company_id != sim_state.company_id:
            error_output(f"Task {task_id} does not belong to your company.")

        # Apply prestige penalty: penalty_cancel_multiplier * reward_prestige_delta across task's required domains
        _cfg = _get_world_cfg()
        cancel_penalty = Decimal(str(_cfg.penalty_cancel_multiplier)) * task.reward_prestige_delta
        reqs = db.query(TaskRequirement).filter(TaskRequirement.task_id == tid).all()
        penalties_applied = {}

        # Apply client trust penalty for cancellation
        trust_delta = 0.0
        if task.client_id is not None:
            ct = db.query(ClientTrust).filter(
                ClientTrust.company_id == sim_state.company_id,
                ClientTrust.client_id == task.client_id,
            ).one_or_none()
            if ct is not None:
                old_level = float(ct.trust_level)
                new_level = max(_cfg.trust_min, old_level - _cfg.trust_cancel_penalty)
                trust_delta = new_level - old_level
                ct.trust_level = Decimal(str(round(new_level, 3)))

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
            "task_id": task.title,
            "status": task.status.value,
            "reason": reason,
            "cancel_penalty_per_domain": float(cancel_penalty),
            "prestige_changes": penalties_applied,
            "trust_delta": trust_delta,
            "bankrupt": bankrupt,
        })
