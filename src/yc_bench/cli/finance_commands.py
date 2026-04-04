from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import typer
from sqlalchemy import and_

from ..db.models.ledger import LedgerCategory, LedgerEntry
from ..db.models.sim_state import SimState
from . import get_db, json_output, error_output

finance_app = typer.Typer(help="Finance and ledger commands.")


def _parse_date(date_str: str) -> datetime:
    try:
        dt = datetime.strptime(date_str, "%m/%d/%Y")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        raise typer.BadParameter(f"Invalid date format: {date_str}. Use MM/DD/YYYY.")


@finance_app.command("ledger")
def finance_ledger(
    from_date: Optional[str] = typer.Option(
        None, "--from", help="Start date MM/DD/YYYY"
    ),
    to_date: Optional[str] = typer.Option(None, "--to", help="End date MM/DD/YYYY"),
    category: Optional[str] = typer.Option(
        None, "--category", help="Filter by ledger category"
    ),
):
    """View ledger entries with optional date and category filters."""
    with get_db() as db:
        sim_state = db.query(SimState).first()
        if sim_state is None:
            error_output("No simulation found.")

        query = db.query(LedgerEntry).filter(
            LedgerEntry.company_id == sim_state.company_id
        )

        if from_date is not None:
            from_dt = _parse_date(from_date)
            query = query.filter(LedgerEntry.occurred_at >= from_dt)

        if to_date is not None:
            to_dt = _parse_date(to_date)
            query = query.filter(LedgerEntry.occurred_at <= to_dt)

        if category is not None:
            try:
                cat = LedgerCategory(category)
            except ValueError:
                error_output(
                    f"Invalid category: {category}. Valid: {[c.value for c in LedgerCategory]}"
                )
            query = query.filter(LedgerEntry.category == cat)

        entries = query.order_by(LedgerEntry.occurred_at.asc()).all()

        total_amount = sum(e.amount_cents for e in entries)

        results = [
            {
                "id": str(e.id),
                "occurred_at": e.occurred_at.isoformat(),
                "category": e.category.value,
                "amount_cents": e.amount_cents,
                "ref_type": e.ref_type,
                "ref_id": str(e.ref_id) if e.ref_id else None,
            }
            for e in entries
        ]

        json_output(
            {
                "count": len(results),
                "total_amount_cents": total_amount,
                "entries": results,
            }
        )
