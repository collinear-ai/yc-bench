from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import typer

from ..core.engine import advance_time
from ..core.events import fetch_next_event, insert_event
from ..db.models.company import Company
from ..db.models.event import EventType
from ..db.models.sim_state import SimState
from ..config import get_world_config
from ..services.seed_world import SeedWorldRequest, seed_world_transactional
from . import get_db, json_output, error_output

sim_app = typer.Typer(help="Simulation initialization commands.")


def _parse_date(date_str: str) -> datetime:
    """Parse MM/DD/YYYY into a timezone-aware datetime at 09:00 UTC."""
    try:
        dt = datetime.strptime(date_str, "%m/%d/%Y")
        return dt.replace(hour=9, minute=0, second=0, tzinfo=timezone.utc)
    except ValueError:
        raise typer.BadParameter(f"Invalid date format: {date_str}. Use MM/DD/YYYY.")


@sim_app.command("init")
def sim_init(
    seed: int = typer.Option(..., help="RNG seed for deterministic generation"),
    start_date: str = typer.Option(..., "--start-date", help="Start date MM/DD/YYYY"),
    horizon_years: int = typer.Option(1, "--horizon-years", help="Simulation horizon in years"),
    company_name: str = typer.Option(..., "--company-name", help="Company name"),
    employee_count: Optional[int] = typer.Option(None, "--employee-count", help="Number of employees (default from experiment config)"),
    market_task_count: Optional[int] = typer.Option(None, "--market-task-count", help="Number of market tasks (default from experiment config)"),
):
    """Initialize a new simulation: seed world, create company, schedule horizon."""
    _wc = get_world_config()
    if employee_count is None:
        employee_count = _wc.num_employees
    if market_task_count is None:
        market_task_count = _wc.num_market_tasks

    start_dt = _parse_date(start_date)
    horizon_end = start_dt.replace(year=start_dt.year + horizon_years)

    with get_db() as db:
        # Check if a simulation already exists
        existing = db.query(SimState).first()
        if existing is not None:
            error_output("A simulation already exists. Only one simulation per database is supported.")

        req = SeedWorldRequest(
            run_seed=seed,
            company_name=company_name,
            horizon_years=horizon_years,
            employee_count=employee_count,
            market_task_count=market_task_count,
            start_date=start_dt,
        )
        result = seed_world_transactional(db, req)

        # Schedule horizon_end event (deterministic id via insert_event helper)
        insert_event(
            db=db,
            company_id=result.company_id,
            event_type=EventType.HORIZON_END,
            scheduled_at=horizon_end,
            payload={"reason": "horizon_end"},
            dedupe_key="horizon_end",
        )

        # Create sim_state row
        sim_state = SimState(
            company_id=result.company_id,
            sim_time=start_dt,
            run_seed=seed,
            horizon_end=horizon_end,
            replenish_counter=0,
        )
        db.add(sim_state)
        db.flush()

        json_output({
            "simulation_id": str(result.company_id),
            "company_id": str(result.company_id),
            "sim_time": start_dt.isoformat(),
            "horizon_end": horizon_end.isoformat(),
            "company_name": company_name,
            "seed": seed,
        })


@sim_app.command("resume")
def sim_resume():
    """Advance simulation to the next queued event checkpoint and return wake results."""
    with get_db() as db:
        sim_state = db.query(SimState).first()
        if sim_state is None:
            error_output("No simulation found. Run `yc-bench sim init` first.")
        company = db.query(Company).filter(Company.id == sim_state.company_id).one()

        next_event = fetch_next_event(
            db=db,
            company_id=sim_state.company_id,
            up_to=sim_state.horizon_end,
        )

        if next_event is None:
            terminal_reason = None
            bankrupt = company.funds_cents < 0
            horizon_reached = sim_state.sim_time >= sim_state.horizon_end
            if bankrupt:
                terminal_reason = "bankruptcy"
            elif horizon_reached:
                terminal_reason = "horizon_end"

            json_output({
                "ok": True,
                "message": "no_pending_events",
                "old_sim_time": sim_state.sim_time.isoformat(),
                "new_sim_time": sim_state.sim_time.isoformat(),
                "events_processed": 0,
                "payrolls_applied": 0,
                "balance_delta": 0,
                "wake_events": [],
                "bankrupt": bankrupt,
                "horizon_reached": horizon_reached,
                "terminal_reason": terminal_reason,
            })
            return

        checkpoint_type = next_event.event_type.value
        result = advance_time(
            db=db,
            company_id=sim_state.company_id,
            target_time=next_event.scheduled_at,
        )

        terminal_reason = None
        if result.bankrupt:
            terminal_reason = "bankruptcy"
        elif result.horizon_reached:
            terminal_reason = "horizon_end"

        payload = result.__dict__.copy()
        payload["ok"] = True
        payload["checkpoint_event_type"] = checkpoint_type
        payload["terminal_reason"] = terminal_reason
        json_output(payload)
