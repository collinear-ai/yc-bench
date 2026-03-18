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
        "trust_reward_formula": "continuous: work_reduction = 0.40 × trust/5.0; cross_client_decay = 0.03/task; tiers: Standard=[0.7,1.0), Premium=[1.0,1.7), Enterprise=[1.7,2.5]; specialty_bias=0.70; RAT clients: scope_creep + payment_disputes above loyalty_reveal_trust",
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
    """Reconstruct prestige history by walking forward from initial prestige.

    Applies task deltas (success/fail) and prestige decay between events.
    """
    from ..db.models.company import CompanyPrestige
    from ..db.models.task import Task, TaskRequirement, TaskStatus
    from ..config import get_world_config

    wc = get_world_config()

    prestige_rows = (
        db.query(CompanyPrestige)
        .filter(CompanyPrestige.company_id == company_id)
        .all()
    )
    if not prestige_rows:
        return []

    all_domains = sorted(
        p.domain.value if hasattr(p.domain, "value") else str(p.domain)
        for p in prestige_rows
    )

    # Get ALL completed tasks (success and fail) ordered by completion time
    completed_tasks = (
        db.query(Task)
        .filter(
            Task.company_id == company_id,
            Task.completed_at.isnot(None),
            Task.status.in_([TaskStatus.COMPLETED_SUCCESS, TaskStatus.COMPLETED_FAIL]),
        )
        .order_by(Task.completed_at)
        .all()
    )

    # Map task -> domains
    task_domain_map: Dict[str, List[str]] = {}
    for t in completed_tasks:
        reqs = db.query(TaskRequirement).filter(TaskRequirement.task_id == t.id).all()
        task_domain_map[str(t.id)] = [
            r.domain.value if hasattr(r.domain, "value") else str(r.domain)
            for r in reqs
        ]

    # Walk forward from initial prestige
    domain_levels = {d: wc.initial_prestige_level for d in all_domains}
    last_event_time = None
    events: List[Dict[str, Any]] = []

    # Record initial state at first task time
    if completed_tasks:
        first_time = completed_tasks[0].completed_at
        for domain in all_domains:
            events.append({
                "time": first_time.isoformat(),
                "domain": domain,
                "level": round(domain_levels[domain], 4),
            })
        last_event_time = first_time

    for t in completed_tasks:
        # Apply decay for ALL domains since last event
        if last_event_time and t.completed_at > last_event_time:
            days = (t.completed_at - last_event_time).total_seconds() / 86400
            decay = wc.prestige_decay_per_day * days
            for d in all_domains:
                domain_levels[d] = max(wc.prestige_min, domain_levels[d] - decay)

        # Apply task delta
        domains = task_domain_map.get(str(t.id), [])
        delta = float(t.reward_prestige_delta) if t.reward_prestige_delta else 0.0
        is_success = (t.status == TaskStatus.COMPLETED_SUCCESS)

        for domain in domains:
            if is_success:
                domain_levels[domain] = min(wc.prestige_max, domain_levels[domain] + delta)
            else:
                penalty = wc.penalty_fail_multiplier * delta
                domain_levels[domain] = max(wc.prestige_min, domain_levels[domain] - penalty)

            events.append({
                "time": t.completed_at.isoformat(),
                "domain": domain,
                "level": round(domain_levels[domain], 4),
            })

        last_event_time = t.completed_at

    return events


def _extract_client_trust(db, company_id: UUID) -> List[Dict[str, Any]]:
    """Reconstruct client trust time series from task completions and decay.

    Walks forward through completed/failed tasks, applying trust gains/losses
    and inter-event decay to reconstruct the full trust curve per client.
    """
    from ..db.models.client import Client, ClientTrust
    from ..db.models.task import Task, TaskStatus
    from ..config import get_world_config

    wc = get_world_config()

    # Get all clients
    trust_rows = (
        db.query(ClientTrust, Client.name)
        .join(Client, Client.id == ClientTrust.client_id)
        .filter(ClientTrust.company_id == company_id)
        .order_by(Client.name)
        .all()
    )
    if not trust_rows:
        return []

    client_names = {str(ct.client_id): name for ct, name in trust_rows}

    # Fetch loyalty scores for post-hoc analysis
    client_loyalty = {}
    for ct_row, _ in trust_rows:
        c = db.query(Client).filter(Client.id == ct_row.client_id).one_or_none()
        if c:
            client_loyalty[str(c.id)] = c.loyalty

    # Get all tasks that affect trust (completed or failed), ordered by completion time
    tasks = (
        db.query(Task)
        .filter(
            Task.company_id == company_id,
            Task.client_id.isnot(None),
            Task.completed_at.isnot(None),
            Task.status.in_([TaskStatus.COMPLETED_SUCCESS, TaskStatus.COMPLETED_FAIL]),
        )
        .order_by(Task.completed_at)
        .all()
    )

    # Initialize trust at 0.0 for all clients
    trust_levels = {str(ct.client_id): 0.0 for ct, _ in trust_rows}
    last_event_time = None

    points = []
    # Record initial state at first task time
    if tasks:
        first_time = tasks[0].completed_at
        for cid, name in client_names.items():
            points.append({
                "time": first_time.isoformat(),
                "client_name": name,
                "trust_level": 0.0,
                "loyalty": client_loyalty.get(cid, 0.0),
            })
        last_event_time = first_time

    for t in tasks:
        cid = str(t.client_id)
        if cid not in trust_levels:
            continue

        # Apply decay for all clients since last event
        if last_event_time and t.completed_at > last_event_time:
            days_elapsed = (t.completed_at - last_event_time).total_seconds() / 86400
            decay = wc.trust_decay_per_day * days_elapsed
            for k in trust_levels:
                trust_levels[k] = max(wc.trust_min, trust_levels[k] - decay)

        # Apply trust change for this task's client
        if t.status == TaskStatus.COMPLETED_SUCCESS:
            ratio = trust_levels[cid] / wc.trust_max
            gain = wc.trust_gain_base * ((1 - ratio) ** wc.trust_gain_diminishing_power)
            trust_levels[cid] = min(wc.trust_max, trust_levels[cid] + gain)
        else:
            trust_levels[cid] = max(wc.trust_min, trust_levels[cid] - wc.trust_fail_penalty)

        # Record state for the affected client
        points.append({
            "time": t.completed_at.isoformat(),
            "client_name": client_names[cid],
            "trust_level": round(trust_levels[cid], 4),
            "loyalty": client_loyalty.get(cid, 0.0),
        })
        last_event_time = t.completed_at

    return points


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
            "advertised_reward_cents": int(t.advertised_reward_cents) if t.advertised_reward_cents else int(t.reward_funds_cents),
            "reward_prestige_delta": float(t.reward_prestige_delta) if t.reward_prestige_delta else 0.0,
            "status": t.status.value if hasattr(t.status, "value") else str(t.status),
            "accepted_at": t.accepted_at.isoformat() if t.accepted_at else None,
            "deadline": t.deadline.isoformat() if t.deadline else None,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
            "domains": domains,
        })

    return result
