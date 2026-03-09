"""Extract structured time-series data from the DB at end-of-run."""
from __future__ import annotations

from typing import Any, Dict, List
from uuid import UUID


def extract_time_series(db_factory, company_id: UUID) -> Dict[str, Any]:
    """Query the DB and return structured time-series for funds, prestige, tasks, ledger."""
    with db_factory() as db:
        funds = _extract_funds(db, company_id)
        prestige = _extract_prestige(db, company_id)
        tasks = _extract_tasks(db, company_id)
        ledger = _extract_ledger(db, company_id)
        client_trust = _extract_client_trust(db, company_id)

    return {
        "funds": funds,
        "prestige": prestige,
        "tasks": tasks,
        "ledger": ledger,
        "client_trust": client_trust,
    }


def _extract_ledger(db, company_id: UUID) -> List[Dict[str, Any]]:
    """Direct dump of ledger_entries ordered by occurred_at."""
    from ..db.models.ledger import LedgerEntry

    entries = (
        db.query(LedgerEntry)
        .filter(LedgerEntry.company_id == company_id)
        .order_by(LedgerEntry.occurred_at)
        .all()
    )
    return [
        {
            "time": e.occurred_at.isoformat(),
            "category": e.category.value if hasattr(e.category, "value") else str(e.category),
            "amount_cents": int(e.amount_cents),
        }
        for e in entries
    ]


def _extract_funds(db, company_id: UUID) -> List[Dict[str, Any]]:
    """Reconstruct running balance from ledger_entries, starting at initial funds."""
    from ..db.models.company import Company
    from ..db.models.ledger import LedgerEntry

    entries = (
        db.query(LedgerEntry)
        .filter(LedgerEntry.company_id == company_id)
        .order_by(LedgerEntry.occurred_at)
        .all()
    )

    company = db.query(Company).filter(Company.id == company_id).first()
    # Derive initial funds by subtracting all ledger entries from current balance
    total_delta = sum(int(e.amount_cents) for e in entries)
    initial_funds = int(company.funds_cents) - total_delta

    points: List[Dict[str, Any]] = []
    if entries:
        # Add start point at the time of first ledger entry
        points.append({
            "time": entries[0].occurred_at.isoformat(),
            "funds_cents": initial_funds,
            "event": "start",
        })

    running = initial_funds
    for e in entries:
        running += int(e.amount_cents)
        category = e.category.value if hasattr(e.category, "value") else str(e.category)
        points.append({
            "time": e.occurred_at.isoformat(),
            "funds_cents": running,
            "event": category,
        })

    return points


def _extract_prestige(db, company_id: UUID) -> List[Dict[str, Any]]:
    """Reconstruct prestige history from final prestige and completed task rewards.

    Works backward: final prestige minus task rewards gives us history points.
    """
    from decimal import Decimal

    from ..db.models.company import CompanyPrestige
    from ..db.models.task import Task, TaskRequirement, TaskStatus

    # Get final prestige levels per domain
    prestige_rows = (
        db.query(CompanyPrestige)
        .filter(CompanyPrestige.company_id == company_id)
        .all()
    )
    final_prestige = {
        p.domain.value if hasattr(p.domain, "value") else str(p.domain): float(p.prestige_level)
        for p in prestige_rows
    }

    # Get completed tasks with their domains and prestige deltas, ordered by completion time
    completed_tasks = (
        db.query(Task)
        .filter(
            Task.company_id == company_id,
            Task.status == TaskStatus.COMPLETED_SUCCESS,
            Task.completed_at.isnot(None),
        )
        .order_by(Task.completed_at)
        .all()
    )

    # For each completed task, find which domains it touches
    task_domain_map: Dict[str, List[str]] = {}
    for t in completed_tasks:
        reqs = db.query(TaskRequirement).filter(TaskRequirement.task_id == t.id).all()
        domains = [
            r.domain.value if hasattr(r.domain, "value") else str(r.domain)
            for r in reqs
        ]
        task_domain_map[str(t.id)] = domains

    # Work backward from final prestige to reconstruct history
    # Start with final prestige, subtract each task's reward_prestige_delta for its domains
    domain_running = dict(final_prestige)
    # Collect events in reverse, then reverse at the end
    reverse_events = []

    for t in reversed(completed_tasks):
        domains = task_domain_map.get(str(t.id), [])
        delta = float(t.reward_prestige_delta) if t.reward_prestige_delta else 0.0
        for domain in domains:
            # Record the "after" state at completion time
            reverse_events.append({
                "time": t.completed_at.isoformat(),
                "domain": domain,
                "level": round(domain_running.get(domain, 1.0), 4),
            })
            # Subtract delta to get "before" state
            domain_running[domain] = domain_running.get(domain, 1.0) - delta

    # Reverse to get chronological order
    events = list(reversed(reverse_events))

    # Prepend initial prestige (level 1.0 for all domains) at earliest task time or just as a starting record
    all_domains = sorted(final_prestige.keys())
    if completed_tasks:
        first_time = completed_tasks[0].completed_at.isoformat()
    elif prestige_rows:
        # No completed tasks — just record final state
        first_time = None
    else:
        first_time = None

    initial_events = []
    if first_time:
        for domain in all_domains:
            initial_events.append({
                "time": first_time,
                "domain": domain,
                "level": round(domain_running.get(domain, 1.0), 4),
            })

    return initial_events + events


def _extract_client_trust(db, company_id: UUID) -> List[Dict[str, Any]]:
    """Extract current client trust levels."""
    from ..db.models.client import Client, ClientTrust

    rows = (
        db.query(ClientTrust, Client.name)
        .join(Client, Client.id == ClientTrust.client_id)
        .filter(ClientTrust.company_id == company_id)
        .order_by(Client.name)
        .all()
    )
    return [
        {
            "client_id": str(ct.client_id),
            "client_name": name,
            "trust_level": float(ct.trust_level),
        }
        for ct, name in rows
    ]


def _extract_tasks(db, company_id: UUID) -> List[Dict[str, Any]]:
    """Query all tasks owned by the company with their requirements."""
    from ..db.models.task import Task, TaskRequirement, TaskStatus

    tasks = (
        db.query(Task)
        .filter(
            Task.company_id == company_id,
            Task.status != TaskStatus.MARKET,
        )
        .order_by(Task.accepted_at)
        .all()
    )

    from ..db.models.client import Client

    result = []
    for t in tasks:
        reqs = db.query(TaskRequirement).filter(TaskRequirement.task_id == t.id).all()
        domains = [
            r.domain.value if hasattr(r.domain, "value") else str(r.domain)
            for r in reqs
        ]

        client_name = None
        if t.client_id is not None:
            client_row = db.query(Client).filter(Client.id == t.client_id).one_or_none()
            if client_row:
                client_name = client_row.name

        result.append({
            "task_id": str(t.id),
            "title": t.title,
            "client_name": client_name,
            "required_prestige": int(t.required_prestige),
            "required_trust": int(t.required_trust) if t.required_trust else 0,
            "reward_funds_cents": int(t.reward_funds_cents),
            "reward_prestige_delta": float(t.reward_prestige_delta) if t.reward_prestige_delta else 0.0,
            "status": t.status.value if hasattr(t.status, "value") else str(t.status),
            "accepted_at": t.accepted_at.isoformat() if t.accepted_at else None,
            "deadline": t.deadline.isoformat() if t.deadline else None,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
            "domains": domains,
        })

    return result
