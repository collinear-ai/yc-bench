from __future__ import annotations

import typer
from sqlalchemy import func

from ..db.models.client import Client, ClientTrust
from ..db.models.ledger import LedgerCategory, LedgerEntry
from ..db.models.sim_state import SimState
from ..db.models.task import Task, TaskStatus
from . import get_db, json_output, error_output

client_app = typer.Typer(help="Client management commands.")


@client_app.command("list")
def client_list():
    """Show all clients with current trust levels."""
    with get_db() as db:
        sim_state = db.query(SimState).first()
        if sim_state is None:
            error_output("No simulation found.")

        clients = db.query(Client).order_by(Client.name).all()
        results = []
        for c in clients:
            ct = db.query(ClientTrust).filter(
                ClientTrust.company_id == sim_state.company_id,
                ClientTrust.client_id == c.id,
            ).one_or_none()
            results.append({
                "client_id": str(c.id),
                "name": c.name,
                "trust_level": float(ct.trust_level) if ct else 0.0,
                "tier": c.tier,
                "specialties": c.specialty_domains or [],
            })

        json_output({
            "count": len(results),
            "clients": results,
        })


@client_app.command("history")
def client_history():
    """Show per-client task history: successes, failures, listed vs actual rewards, disputes."""
    with get_db() as db:
        sim_state = db.query(SimState).first()
        if sim_state is None:
            error_output("No simulation found.")

        company_id = sim_state.company_id
        clients = db.query(Client).order_by(Client.name).all()
        results = []

        for c in clients:
            # Count successes and failures
            success_count = db.query(func.count(Task.id)).filter(
                Task.company_id == company_id,
                Task.client_id == c.id,
                Task.status == TaskStatus.COMPLETED_SUCCESS,
            ).scalar() or 0

            fail_count = db.query(func.count(Task.id)).filter(
                Task.company_id == company_id,
                Task.client_id == c.id,
                Task.status == TaskStatus.COMPLETED_FAIL,
            ).scalar() or 0

            ct = db.query(ClientTrust).filter(
                ClientTrust.company_id == company_id,
                ClientTrust.client_id == c.id,
            ).one_or_none()

            total = success_count + fail_count
            fail_rate = round(fail_count / total * 100, 1) if total > 0 else 0.0

            results.append({
                "client_name": c.name,
                "trust_level": float(ct.trust_level) if ct else 0.0,
                "tasks_succeeded": success_count,
                "tasks_failed": fail_count,
                "failure_rate_pct": fail_rate,
            })

        json_output({
            "count": len(results),
            "client_history": results,
        })
