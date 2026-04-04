from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import typer
from sqlalchemy import and_

from ..db.models.session import MonthlyMetric
from ..db.models.sim_state import SimState
from . import get_db, json_output, error_output

report_app = typer.Typer(help="Reporting commands.")


def _parse_month(month_str: str) -> date:
    """Parse YYYY-MM into a date (first of month)."""
    try:
        dt = datetime.strptime(month_str, "%Y-%m")
        return dt.date().replace(day=1)
    except ValueError:
        raise typer.BadParameter(f"Invalid month format: {month_str}. Use YYYY-MM.")


@report_app.command("monthly")
def report_monthly(
    from_month: Optional[str] = typer.Option(
        None, "--from-month", help="Start month YYYY-MM"
    ),
    to_month: Optional[str] = typer.Option(
        None, "--to-month", help="End month YYYY-MM"
    ),
):
    """View monthly metrics (revenue, cost, return, ending funds)."""
    with get_db() as db:
        sim_state = db.query(SimState).first()
        if sim_state is None:
            error_output("No simulation found.")

        query = db.query(MonthlyMetric).filter(
            MonthlyMetric.company_id == sim_state.company_id
        )

        if from_month is not None:
            from_dt = _parse_month(from_month)
            query = query.filter(MonthlyMetric.month_start >= from_dt)

        if to_month is not None:
            to_dt = _parse_month(to_month)
            query = query.filter(MonthlyMetric.month_start <= to_dt)

        metrics = query.order_by(MonthlyMetric.month_start.asc()).all()

        results = [
            {
                "month_start": m.month_start.isoformat(),
                "revenue_cents": m.revenue_cents,
                "cost_cents": m.cost_cents,
                "return_cents": m.return_cents,
                "ending_funds_cents": m.ending_funds_cents,
            }
            for m in metrics
        ]

        json_output(
            {
                "count": len(results),
                "months": results,
            }
        )
