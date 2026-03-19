from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from .business_time import business_hours_between
from ..db.models.company import Domain
from ..db.models.employee import EmployeeSkillRate
from ..db.models.task import TaskRequirement, Task, TaskAssignment, TaskStatus

@dataclass(frozen=True)
class RequirementState:
    domain: str
    required_qty: Decimal
    completed_qty: Decimal
    
@dataclass(frozen=True)
class TaskProgressState:
    task_id: str
    status: str
    requirements: tuple[RequirementState, ...]

@dataclass(frozen=True)
class AssignmentState:
    task_id: str
    employee_id: str

@dataclass(frozen=True)
class EmployeeRateState:
    employee_id: str
    domain: str
    rate_domain_per_hour: Decimal

@dataclass(frozen=True)
class ProgressDelta:
    task_id: str
    domain: str
    delta_qty: Decimal
    before_qty: Decimal
    after_qty: Decimal

@dataclass(frozen=True)
class TaskProgressSummary:
    task_id: str
    ratio_before: Decimal
    ratio_after: Decimal
    completed: bool

@dataclass(frozen=True)
class EffectiveRate:
    task_id: UUID
    domain: Domain
    rate_per_hour: Decimal

def _active_assignment_count(assignments):
    counts = {}
    for a in assignments:
        counts[a.employee_id] = counts.get(a.employee_id, 0) + 1
    return counts

def _rates_by_employee_domain(rates):
    m = {}
    for r in rates:
        m[(r.employee_id, r.domain)] = r.rate_domain_per_hour
    return m

_EFFICIENT_TEAM_SIZE = 4       # first N employees at full rate
_OVERCROWD_PENALTY = Decimal("0")  # employees beyond N contribute nothing (pure overhead)


def _effective_rate_for_task_domain(*, task_id, domain, assignments,
                                    assignment_counts, base_rates):
    """Compute effective rate for one task+domain.

    Throughput split uses sqrt(k) instead of k: two concurrent tasks each run at
    1/sqrt(2) ≈ 71% speed, not 50%. This makes mild parallelism (2-3 tasks)
    more efficient than strict sequential.

    Brooks's Law: first 4 employees contribute full rate. Beyond that,
    additional employees contribute at 25% (overcrowding overhead).
    """
    from math import sqrt

    # Collect (employee_id, effective_base) for this task, sorted best-first
    contributions = []
    for a in assignments:
        if a.task_id != task_id:
            continue
        k = assignment_counts.get(a.employee_id, 0)
        if k <= 0:
            continue
        base = base_rates.get((a.employee_id, domain), Decimal("0"))
        split_rate = base / Decimal(str(round(sqrt(k), 6)))
        contributions.append(split_rate)

    # Sort best contributors first so they get full rate
    contributions.sort(reverse=True)

    total = Decimal("0")
    for i, rate in enumerate(contributions):
        if i < _EFFICIENT_TEAM_SIZE:
            total += rate
        else:
            total += rate * _OVERCROWD_PENALTY
    return total

def _weighted_ratio_from_rows(rows, *, task_id_label):
    total_completed = Decimal("0")
    total_required = Decimal("0")
    for req in rows:
        if req.required_qty <= 0:
            raise ValueError(
                f"Task {task_id_label} requirement {req.domain} has quantity {req.required_qty}"
            )
        completed = req.completed_qty
        if completed < 0:
            raise ValueError(
                f"Task {task_id_label} requirement {req.domain} has completed quantity {req.completed_qty} which is less than 0"
            )
        if completed > req.required_qty:
            completed = req.required_qty
            
        total_completed += completed
        total_required += req.required_qty

    if total_required == 0:
        return Decimal("0")
    return total_completed / total_required

def task_progress_ratio(task):
    if not task.requirements:
        raise ValueError(f"Task {task.task_id} has no requirements")
    return _weighted_ratio_from_rows(task.requirements, task_id_label=task.task_id)

def apply_progress_window(*, tasks, assignments, rates, t0, t1):
    hours = Decimal(str(business_hours_between(t0, t1)))
    if hours <= 0:
        unchanged = list(tasks)
        summaries = [
            TaskProgressSummary(
                task_id=t.task_id,
                ratio_before=task_progress_ratio(t),
                ratio_after=task_progress_ratio(t),
                completed=all(r.completed_qty >= r.required_qty for r in t.requirements),
            )
            for t in unchanged
        ]
        return unchanged, [], summaries
    
    assignment_list = list(assignments)
    assignment_counts = _active_assignment_count(assignment_list)
    base_rates = _rates_by_employee_domain(rates)

    updated_tasks = []
    deltas = []
    summaries = []

    for task in tasks:
        ratio_before = task_progress_ratio(task)

        if task.status not in {"planned", "active"}:
            updated_tasks.append(task)
            summaries.append(
                TaskProgressSummary(
                    task_id=task.task_id,
                    ratio_before=ratio_before,
                    ratio_after=ratio_before,
                    completed=all(r.completed_qty >= r.required_qty for r in task.requirements),
                )
            )
            continue
        
        next_requirements = []
        for req in task.requirements:
            before = Decimal(req.completed_qty)
            required = Decimal(req.required_qty)

            eff_rate = _effective_rate_for_task_domain(
                task_id=task.task_id,
                domain=req.domain,
                assignments=assignment_list,
                assignment_counts=assignment_counts,
                base_rates=base_rates,
            )
            delta = eff_rate * hours
            after = before + delta
            # Progress cap is independent from deadline/failure logic.
            if after > required:
                after = required
            # Guardrail in case of inconsistent negative upstream data.
            if after < 0:
                after = Decimal("0")

            next_requirements.append(RequirementState(
                domain=req.domain,
                required_qty=required,
                completed_qty=after,
            ))
        
            deltas.append(ProgressDelta(
                task_id=task.task_id,
                domain=req.domain,
                delta_qty=after - before,
                before_qty=before,
                after_qty=after,
            ))

        next_task = TaskProgressState(
            task_id=task.task_id,
            status=task.status,
            requirements=tuple(next_requirements),
        )
        ratio_after = task_progress_ratio(next_task)
        completed = all(r.completed_qty >= r.required_qty for r in next_requirements)
        updated_tasks.append(next_task)
        summaries.append(
            TaskProgressSummary(
                task_id=task.task_id,
                ratio_before=ratio_before,
                ratio_after=ratio_after,
                completed=completed,
            )
        )
    return updated_tasks, deltas, summaries

def compute_task_progress_ratio(db, task_id):
    reqs = db.query(TaskRequirement).filter(TaskRequirement.task_id == task_id).all()
    if not reqs:
        return Decimal("0")
    return _weighted_ratio_from_rows(reqs, task_id_label=task_id)

def compute_effective_rates(db, company_id):
    active_tasks = db.query(Task).filter(Task.company_id == company_id, Task.status == TaskStatus.ACTIVE).all()
    if not active_tasks:
        return []
    
    task_ids = [t.id for t in active_tasks]
    requirements = db.query(TaskRequirement).filter(TaskRequirement.task_id.in_(task_ids)).all()
    assignments = db.query(TaskAssignment).filter(TaskAssignment.task_id.in_(task_ids)).all()

    if not assignments:
        out = []
        for req in requirements:
            out.append(EffectiveRate(
                task_id=req.task_id,
                domain=req.domain,
                rate_per_hour=Decimal("0"),
            ))
        return out
    
    assignment_counts = {}
    assignments_by_task = {}
    for a in assignments:
        assignments_by_task.setdefault(a.task_id, []).append(a)
        assignment_counts[a.employee_id] = assignment_counts.get(a.employee_id, 0) + 1

    employee_ids = list(assignment_counts.keys())
    skill_rows = db.query(EmployeeSkillRate).filter(EmployeeSkillRate.employee_id.in_(employee_ids)).all()

    base_rates = {}
    for s in skill_rows:
        base_rates[(s.employee_id, s.domain)] = Decimal(s.rate_domain_per_hour)

    out = []
    for req in requirements:
        from math import sqrt
        contributions = []
        for a in assignments_by_task.get(req.task_id, []):
            k = assignment_counts.get(a.employee_id, 0)
            if k <= 0:
                continue
            base = base_rates.get((a.employee_id, req.domain), Decimal("0"))
            split_rate = base / Decimal(str(round(sqrt(k), 6)))
            contributions.append(split_rate)

        contributions.sort(reverse=True)
        total = Decimal("0")
        for i, rate in enumerate(contributions):
            if i < _EFFICIENT_TEAM_SIZE:
                total += rate
            else:
                total += rate * _OVERCROWD_PENALTY

        out.append(EffectiveRate(
            task_id=req.task_id,
            domain=req.domain,
            rate_per_hour=total,
        ))
    return out

def flush_progress(db, company_id, t0, t1):
    active_tasks = db.query(Task).filter(Task.company_id == company_id, Task.status == TaskStatus.ACTIVE).all()
    if not active_tasks:
        return
    task_ids = [t.id for t in active_tasks]
    req_rows = db.query(TaskRequirement).filter(TaskRequirement.task_id.in_(task_ids)).all()
    asg_rows = db.query(TaskAssignment).filter(TaskAssignment.task_id.in_(task_ids)).all()
    emp_ids = list({a.employee_id for a in asg_rows})
    rate_rows = db.query(EmployeeSkillRate).filter(EmployeeSkillRate.employee_id.in_(emp_ids)).all()

    reqs_by_task = {}
    req_index = {}
    for r in req_rows:
        req_index[(r.task_id, r.domain)] = r
        reqs_by_task.setdefault(r.task_id, []).append(
            RequirementState(
                domain=r.domain,
                required_qty=Decimal(r.required_qty),
                completed_qty=Decimal(r.completed_qty),
            )
        )
    
    task_states = [
        TaskProgressState(
            task_id=t.id,
            status=t.status.value if hasattr(t.status, "value") else str(t.status),
            requirements=tuple(reqs_by_task.get(t.id, [])),
        )
        for t in active_tasks
    ]
    assignments_states = [
        AssignmentState(
            task_id=a.task_id,
            employee_id=a.employee_id,
        )
        for a in asg_rows
    ]
    employee_rate_states = [
        EmployeeRateState(
            employee_id=s.employee_id,
            domain=s.domain,
            rate_domain_per_hour=Decimal(s.rate_domain_per_hour),
        )
        for s in rate_rows
    ]

    updated_tasks, _, _ = apply_progress_window(tasks=task_states, assignments=assignments_states, rates=employee_rate_states, t0=t0, t1=t1)

    for ut in updated_tasks:
        for req in ut.requirements:
            row = req_index[(ut.task_id, req.domain)]
            if row is not None:
                row.completed_qty = req.completed_qty

    db.flush()
