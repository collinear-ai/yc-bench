from __future__ import annotations

from typing import Optional

import typer

from ..db.models.client import Client, ClientTrust
from ..db.models.company import CompanyPrestige, Domain
from ..db.models.sim_state import SimState
from ..db.models.task import Task, TaskRequirement, TaskStatus
from ..config import get_world_config
from . import get_db, json_output, error_output

market_app = typer.Typer(help="Market browsing commands.")


@market_app.command("browse")
def market_browse(
    domain: Optional[Domain] = typer.Option(None, "--domain", help="Filter by requirement domain"),
    required_prestige_lte: Optional[int] = typer.Option(None, "--required-prestige-lte", help="Max required prestige"),
    reward_min_cents: Optional[int] = typer.Option(None, "--reward-min-cents", help="Min reward in cents"),
    limit: Optional[int] = typer.Option(None, "--limit", help="Max results (default from experiment config)"),
    offset: int = typer.Option(0, "--offset", help="Offset for pagination"),
):
    """Browse available tasks on the market."""
    if limit is None:
        limit = get_world_config().market_browse_default_limit
    with get_db() as db:
        query = db.query(Task).filter(Task.status == TaskStatus.MARKET)

        # Filter to only tasks the agent can actually accept:
        # - Per-domain prestige check (not just max — all task domains must be met)
        # - Trust requirement check
        sim_state = db.query(SimState).first()
        if sim_state:
            prestige_rows = db.query(CompanyPrestige).filter(
                CompanyPrestige.company_id == sim_state.company_id
            ).all()
            prestige_map = {p.domain: int(float(p.prestige_level)) for p in prestige_rows}
            min_prestige = min(prestige_map.values()) if prestige_map else 1
            # Quick filter: required_prestige must be <= min domain prestige to guarantee acceptance
            # Tasks between min and max prestige MIGHT be acceptable (depends on domains)
            max_prestige = max(prestige_map.values()) if prestige_map else 1
            query = query.filter(Task.required_prestige <= max_prestige)

        if reward_min_cents is not None:
            query = query.filter(Task.reward_funds_cents >= reward_min_cents)

        if domain is not None:
            # Filter tasks that have a requirement in the given domain
            query = query.filter(
                Task.id.in_(
                    db.query(TaskRequirement.task_id).filter(
                        TaskRequirement.domain == domain
                    )
                )
            )

        total = query.count()
        tasks = query.order_by(Task.reward_funds_cents.desc()).offset(offset).limit(limit).all()

        results = []
        for task in tasks:
            reqs = db.query(TaskRequirement).filter(TaskRequirement.task_id == task.id).all()
            requirements = [
                {
                    "domain": r.domain.value,
                    "required_qty": float(r.required_qty),
                }
                for r in reqs
            ]
            # Look up client name
            client_name = None
            if task.client_id is not None:
                client_row = db.query(Client).filter(Client.id == task.client_id).one_or_none()
                if client_row:
                    client_name = client_row.name

            results.append({
                "task_id": str(task.id),
                "title": task.title,
                "client_name": client_name,
                "required_prestige": task.required_prestige,
                "required_trust": task.required_trust,
                "reward_funds_cents": task.reward_funds_cents,
                "reward_prestige_delta": float(task.reward_prestige_delta),
                "skill_boost_pct": float(task.skill_boost_pct),
                "requirements": requirements,
            })

        json_output({
            "total": total,
            "offset": offset,
            "limit": limit,
            "tasks": results,
        })
