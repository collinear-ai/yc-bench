from __future__ import annotations

import typer

from ..db.models.client import Client, ClientTrust
from ..db.models.sim_state import SimState
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
            })

        json_output({
            "count": len(results),
            "clients": results,
        })
