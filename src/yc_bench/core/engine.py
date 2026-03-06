"""Simulation engine: advance time with deterministic event processing.

Main loop:
1. Find next action: earliest of (next_event, next_payroll_boundary, target_time)
2. Flush progress from current_time to action_time
3. If payroll: deduct salaries, write ledger entries, bankruptcy check
4. If event: dispatch to handler, consume, bankruptcy check
5. Loop until target or terminal condition

Payroll-event tie-breaking: payroll first at same timestamp (start-of-day obligation).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from ..db.models.company import Company, CompanyPrestige
from ..db.models.employee import Employee
from ..db.models.event import EventType, SimEvent
from ..db.models.ledger import LedgerCategory, LedgerEntry
from ..db.models.sim_state import SimState
from ..config import get_world_config
from .business_time import iter_monthly_payroll_boundaries
from .eta import recalculate_etas
from .events import consume_event, fetch_next_event, insert_event
from .handlers.bankruptcy import handle_bankruptcy
from .handlers.horizon_end import handle_horizon_end
from .handlers.task_complete import handle_task_complete
from .handlers.task_half import handle_task_half
from .progress import flush_progress


@dataclass
class AdvanceResult:
    old_sim_time: str
    new_sim_time: str
    events_processed: int = 0
    payrolls_applied: int = 0
    balance_delta: int = 0
    bankrupt: bool = False
    horizon_reached: bool = False
    wake_events: List[Dict] = field(default_factory=list)


def apply_payroll(db: Session, company_id: UUID, time: datetime) -> bool:
    """Deduct monthly salaries for all employees. Returns True if bankrupt after payroll."""
    company = db.query(Company).filter(Company.id == company_id).one()
    employees = db.query(Employee).filter(Employee.company_id == company_id).all()

    total_payroll = 0
    for emp in employees:
        salary = int(emp.salary_cents)
        total_payroll += salary
        db.add(LedgerEntry(
            company_id=company_id,
            occurred_at=time,
            category=LedgerCategory.MONTHLY_PAYROLL,
            amount_cents=-salary,
            ref_type="employee",
            ref_id=emp.id,
        ))

    company.funds_cents -= total_payroll
    db.flush()

    return company.funds_cents < 0


def dispatch_event(db: Session, event: SimEvent, sim_time: datetime, company_id: UUID) -> Dict:
    """Route event to appropriate handler. Returns result dict."""
    if event.event_type == EventType.TASK_HALF_PROGRESS:
        result = handle_task_half(db, event)
        # Recalculate ETAs so the next milestone is scheduled
        from ..config import get_world_config
        recalculate_etas(db, company_id, sim_time, milestones=get_world_config().task_progress_milestones)
        return {"type": "task_half", "task_id": str(result.task_id), "milestone_pct": result.milestone_pct, "handled": result.handled}

    elif event.event_type == EventType.TASK_COMPLETED:
        result = handle_task_complete(db, event, sim_time)
        # Recalculate ETAs — freed employees change topology
        from ..config import get_world_config
        recalculate_etas(db, company_id, sim_time, milestones=get_world_config().task_progress_milestones)
        return {
            "type": "task_completed",
            "task_id": str(result.task_id),
            "success": result.success,
            "funds_delta": result.funds_delta,
            "bankrupt": result.bankrupt,
        }

    elif event.event_type == EventType.HORIZON_END:
        result = handle_horizon_end(db, event)
        return {"type": "horizon_end", "reached": result.reached}

    elif event.event_type == EventType.BANKRUPTCY:
        result = handle_bankruptcy(db, event)
        return {"type": "bankruptcy", "bankrupt": result.bankrupt}

    return {"type": "unknown", "event_type": event.event_type.value}


def apply_prestige_decay(db: Session, company_id: UUID, days_elapsed: float) -> None:
    """Reduce prestige in all domains by decay_rate × days. Floors at prestige_min."""
    wc = get_world_config()
    if wc.prestige_decay_per_day <= 0 or days_elapsed <= 0:
        return
    decay = Decimal(str(wc.prestige_decay_per_day * days_elapsed))
    floor = Decimal(str(wc.prestige_min))
    rows = db.query(CompanyPrestige).filter(CompanyPrestige.company_id == company_id).all()
    for row in rows:
        row.prestige_level = max(floor, row.prestige_level - decay)
    db.flush()


def advance_time(
    db: Session,
    company_id: UUID,
    target_time: datetime,
) -> AdvanceResult:
    """Advance simulation from current sim_time to target_time, processing all events and payroll."""
    sim_state = db.query(SimState).filter(SimState.company_id == company_id).one()
    current_time = sim_state.sim_time
    old_time = current_time

    company = db.query(Company).filter(Company.id == company_id).one()
    starting_funds = company.funds_cents

    result = AdvanceResult(
        old_sim_time=old_time.isoformat(),
        new_sim_time=target_time.isoformat(),
    )

    # Pre-compute payroll boundaries
    payroll_times = iter_monthly_payroll_boundaries(current_time, target_time)
    payroll_idx = 0

    while True:
        # Find next payroll
        next_payroll = None
        if payroll_idx < len(payroll_times):
            next_payroll = payroll_times[payroll_idx]

        # Find next event
        next_event = fetch_next_event(db, company_id, target_time)

        # Determine next action time
        candidates = []
        if next_payroll is not None and next_payroll <= target_time:
            candidates.append(("payroll", next_payroll))
        if next_event is not None:
            candidates.append(("event", next_event.scheduled_at))
        candidates.append(("target", target_time))

        # Sort: earliest time first; at same time, payroll before event before target
        action_priority = {"payroll": 0, "event": 1, "target": 2}
        candidates.sort(key=lambda c: (c[1], action_priority[c[0]]))

        action_type, action_time = candidates[0]

        # Flush progress and apply prestige decay from current_time to action_time
        if action_time > current_time:
            days_elapsed = (action_time - current_time).total_seconds() / 86400.0
            flush_progress(db, company_id, current_time, action_time)
            apply_prestige_decay(db, company_id, days_elapsed)
            current_time = action_time

        if action_type == "target":
            # Nothing due before/equal target; stop advancement.
            break

        if action_type == "payroll":
            bankrupt = apply_payroll(db, company_id, current_time)
            result.payrolls_applied += 1
            payroll_idx += 1

            if bankrupt:
                # Insert bankruptcy event at this time
                insert_event(
                    db, company_id,
                    EventType.BANKRUPTCY,
                    current_time,
                    {"reason": "funds_negative_after_payroll"},
                    dedupe_key=f"bankruptcy:{current_time.isoformat()}",
                )
                result.bankrupt = True
                break

        elif action_type == "event":
            event_result = dispatch_event(db, next_event, current_time, company_id)
            consume_event(db, next_event)
            result.events_processed += 1
            result.wake_events.append(event_result)

            # Check terminal conditions
            if next_event.event_type == EventType.HORIZON_END:
                result.horizon_reached = True
                break
            if next_event.event_type == EventType.BANKRUPTCY:
                result.bankrupt = True
                break
            if event_result.get("bankrupt", False):
                result.bankrupt = True
                break

        # Continue loop: more due actions can still exist at the same timestamp.

    # Update sim_time
    sim_state.sim_time = current_time
    db.flush()

    # Compute balance delta
    company = db.query(Company).filter(Company.id == company_id).one()
    result.balance_delta = company.funds_cents - starting_funds
    result.new_sim_time = current_time.isoformat()

    return result


__all__ = [
    "AdvanceResult",
    "advance_time",
    "apply_payroll",
    "dispatch_event",
]
