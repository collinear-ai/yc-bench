"""ETA solver: compute task completion and halfway times, manage projection events.

Projection events (task_completed, task_half_progress) are inserted into sim_events
and recalculated whenever the topology changes (assign, dispatch, cancel, complete).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Set
from uuid import UUID

from sqlalchemy.orm import Session

from ..db.models.company import Domain
from ..db.models.event import EventType, SimEvent
from ..db.models.task import Task, TaskRequirement, TaskStatus
from .business_time import add_business_hours
from .events import insert_event
from .progress import EffectiveRate, compute_effective_rates


def solve_task_completion_time(
    db: Session,
    task_id: UUID,
    now: datetime,
    rates: List[EffectiveRate],
) -> Optional[datetime]:
    """Solve for the business-time at which a task will complete.

    Completion = all domains reach 100%. Time is max(remaining[d] / rate[d]) across domains.
    Returns None if any domain has remaining > 0 and rate == 0 (impossible to complete).
    """
    reqs = db.query(TaskRequirement).filter(
        TaskRequirement.task_id == task_id
    ).all()

    if not reqs:
        return None

    # Build rate map for this task
    rate_map: Dict[Domain, Decimal] = {}
    for r in rates:
        if r.task_id == task_id:
            rate_map[r.domain] = r.rate_per_hour

    max_hours = Decimal("0")
    for req in reqs:
        remaining = req.required_qty - req.completed_qty
        if remaining <= 0:
            continue
        rate = rate_map.get(req.domain, Decimal("0"))
        if rate <= 0:
            return None  # Can't complete this domain
        hours = remaining / rate
        if hours > max_hours:
            max_hours = hours

    if max_hours <= 0:
        # Already complete
        return now

    return add_business_hours(now, max_hours)


def solve_task_halfway_time(
    db: Session,
    task_id: UUID,
    now: datetime,
    rates: List[EffectiveRate],
    half_threshold: float = 0.5,
) -> Optional[datetime]:
    """Solve for the business-time at which weighted progress ratio >= 0.5.

    Weighted ratio is:
      sum_d(completed_d) / sum_d(required_d)

    Each domain progresses linearly until capped at required_d.
    Returns None if reaching 50% is impossible.
    """
    reqs = db.query(TaskRequirement).filter(
        TaskRequirement.task_id == task_id
    ).all()

    if not reqs:
        return None

    rate_map: Dict[Domain, Decimal] = {}
    for r in rates:
        if r.task_id == task_id:
            rate_map[r.domain] = r.rate_per_hour

    total_required = sum((req.required_qty for req in reqs), Decimal("0"))
    if total_required <= 0:
        return now
    target_completed = Decimal(str(half_threshold)) * total_required

    # Check current weighted progress
    current_completed = Decimal("0")
    for req in reqs:
        current_completed += min(req.required_qty, req.completed_qty)

    if current_completed >= target_completed:
        return now

    # Build list of domain progression pieces.
    # cap_hours = hours until domain reaches required_qty
    domains = []
    for req in reqs:
        remaining = req.required_qty - req.completed_qty
        rate = rate_map.get(req.domain, Decimal("0"))
        if remaining > 0 and rate <= 0:
            # This domain can never progress further
            cap_hours = None
        elif remaining <= 0:
            cap_hours = Decimal("0")
        else:
            cap_hours = remaining / rate
        domains.append({
            "completed": req.completed_qty,
            "required": req.required_qty,
            "rate": rate,
            "cap_hours": cap_hours,
        })

    # Piecewise-linear solver over cap breakpoints.
    # In each segment, d(current_completed)/dh = sum(rate_d) for uncapped domains.
    breakpoints = sorted(set(
        d["cap_hours"] for d in domains
        if d["cap_hours"] is not None and d["cap_hours"] > 0
    ))

    h = Decimal("0")
    completed_sum = current_completed

    for bp in breakpoints:
        # Slope of completed_sum during [h, bp]
        slope = Decimal("0")
        for d in domains:
            if d["cap_hours"] is not None and d["cap_hours"] > h:
                slope += d["rate"]

        if slope <= 0:
            # No further progress in this segment.
            if completed_sum >= target_completed:
                return add_business_hours(now, h)
            h = bp
            # Re-evaluate completed amount at breakpoint.
            completed_sum = Decimal("0")
            for d in domains:
                progress = min(d["required"], d["completed"] + d["rate"] * h)
                completed_sum += progress
            continue

        needed = target_completed - completed_sum
        delta_h = needed / slope

        if h + delta_h <= bp:
            return add_business_hours(now, h + delta_h)

        completed_sum += slope * (bp - h)
        h = bp

    # After all breakpoints, check remaining slope
    slope = Decimal("0")
    for d in domains:
        if d["cap_hours"] is not None and d["cap_hours"] > h:
            slope += d["rate"]

    if slope > 0:
        needed = target_completed - completed_sum
        if needed <= 0:
            return add_business_hours(now, h)
        delta_h = needed / slope
        return add_business_hours(now, h + delta_h)

    if completed_sum >= target_completed:
        return add_business_hours(now, h)

    return None  # Cannot reach 50%


def recalculate_etas(
    db: Session,
    company_id: UUID,
    now: datetime,
    impacted_task_ids: Optional[Set[UUID]] = None,
    milestones: Optional[List[float]] = None,
    # Legacy single-threshold parameter — ignored if milestones is provided.
    half_threshold: float = 0.5,
) -> None:
    """Recalculate projection events for active tasks.

    1. Delete stale projection events for impacted tasks (or all if None).
    2. Compute effective rates.
    3. For each active task, solve completion and milestone times.
    4. Insert new projection events.
    """
    if milestones is None:
        milestones = [half_threshold]

    # Determine which tasks to recalculate
    if impacted_task_ids is None:
        active_tasks = db.query(Task).filter(
            Task.company_id == company_id,
            Task.status == TaskStatus.ACTIVE,
        ).all()
        task_ids = {t.id for t in active_tasks}
    else:
        task_ids = impacted_task_ids

    if not task_ids:
        return

    # Delete stale unconsumed projection events for these tasks
    for tid in task_ids:
        stale = db.query(SimEvent).filter(
            SimEvent.company_id == company_id,
            SimEvent.consumed == False,
            SimEvent.event_type.in_([EventType.TASK_COMPLETED, EventType.TASK_HALF_PROGRESS]),
            SimEvent.dedupe_key.like(f"task:{tid}:%"),
        ).all()
        for ev in stale:
            db.delete(ev)

    db.flush()

    # Compute rates for all active tasks (topology-wide, since employee sharing matters)
    rates = compute_effective_rates(db, company_id)

    for tid in task_ids:
        task = db.query(Task).filter(Task.id == tid).one_or_none()
        if task is None or task.status != TaskStatus.ACTIVE:
            continue

        # Completion ETA
        completion_time = solve_task_completion_time(db, tid, now, rates)
        if completion_time is not None:
            insert_event(
                db,
                company_id=company_id,
                event_type=EventType.TASK_COMPLETED,
                scheduled_at=completion_time,
                payload={"task_id": str(tid)},
                dedupe_key=f"task:{tid}:completed",
            )

        # Progress milestone ETAs — skip milestones already emitted
        emitted_pct = task.progress_milestone_pct or 0
        for milestone in sorted(milestones):
            milestone_pct = int(milestone * 100)
            if milestone_pct <= emitted_pct:
                continue
            milestone_time = solve_task_halfway_time(db, tid, now, rates, half_threshold=milestone)
            if milestone_time is not None:
                insert_event(
                    db,
                    company_id=company_id,
                    event_type=EventType.TASK_HALF_PROGRESS,
                    scheduled_at=milestone_time,
                    payload={"task_id": str(tid), "milestone_pct": milestone_pct},
                    dedupe_key=f"task:{tid}:milestone:{milestone_pct}",
                )
                # Only insert the next upcoming milestone — it will be the
                # earliest event; once consumed, recalculate_etas runs again
                # and inserts the following one.
                break

    db.flush()


__all__ = [
    "solve_task_completion_time",
    "solve_task_halfway_time",
    "recalculate_etas",
]
