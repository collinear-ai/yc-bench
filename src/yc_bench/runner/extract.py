"""Extract structured time-series data from the DB at end-of-run."""

from __future__ import annotations

from typing import Any, Dict, List
from uuid import UUID


def extract_time_series(db_factory, company_id: UUID) -> Dict[str, Any]:
    """Query the DB and return all structured data for analysis and plotting."""
    with db_factory() as db:
        funds = _extract_funds(db, company_id)
        prestige = _extract_prestige(db, company_id)
        tasks = _extract_tasks(db, company_id)
        ledger = _extract_ledger(db, company_id)
        client_trust = _extract_client_trust(db, company_id)
        employees = _extract_employees(db, company_id)
        assignments = _extract_assignments(db, company_id)
        clients = _extract_clients(db, company_id)
        scratchpad = _extract_scratchpad(db, company_id)
        config = _extract_config()

    return {
        "funds": funds,
        "prestige": prestige,
        "tasks": tasks,
        "ledger": ledger,
        "client_trust": client_trust,
        "employees": employees,
        "assignments": assignments,
        "clients": clients,
        "scratchpad": scratchpad,
        "config": config,
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
            "category": (
                e.category.value if hasattr(e.category, "value") else str(e.category)
            ),
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
        points.append(
            {
                "time": entries[0].occurred_at.isoformat(),
                "funds_cents": initial_funds,
                "event": "start",
            }
        )

    running = initial_funds
    for e in entries:
        running += int(e.amount_cents)
        category = e.category.value if hasattr(e.category, "value") else str(e.category)
        points.append(
            {
                "time": e.occurred_at.isoformat(),
                "funds_cents": running,
                "event": category,
            }
        )

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
        db.query(CompanyPrestige).filter(CompanyPrestige.company_id == company_id).all()
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
            events.append(
                {
                    "time": first_time.isoformat(),
                    "domain": domain,
                    "level": round(domain_levels[domain], 4),
                }
            )
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
        is_success = t.status == TaskStatus.COMPLETED_SUCCESS

        for domain in domains:
            if is_success:
                domain_levels[domain] = min(
                    wc.prestige_max, domain_levels[domain] + delta
                )
            else:
                penalty = wc.penalty_fail_multiplier * delta
                domain_levels[domain] = max(
                    wc.prestige_min, domain_levels[domain] - penalty
                )

            events.append(
                {
                    "time": t.completed_at.isoformat(),
                    "domain": domain,
                    "level": round(domain_levels[domain], 4),
                }
            )

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
            points.append(
                {
                    "time": first_time.isoformat(),
                    "client_name": name,
                    "trust_level": 0.0,
                    "loyalty": client_loyalty.get(cid, 0.0),
                }
            )
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
            trust_levels[cid] = max(
                wc.trust_min, trust_levels[cid] - wc.trust_fail_penalty
            )

        # Record state for the affected client
        points.append(
            {
                "time": t.completed_at.isoformat(),
                "client_name": client_names[cid],
                "trust_level": round(trust_levels[cid], 4),
                "loyalty": client_loyalty.get(cid, 0.0),
            }
        )
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

        result.append(
            {
                "task_id": str(t.id),
                "title": t.title,
                "client_name": client_name,
                "required_prestige": int(t.required_prestige),
                "required_trust": int(t.required_trust) if t.required_trust else 0,
                "reward_funds_cents": int(t.reward_funds_cents),
                "advertised_reward_cents": (
                    int(t.advertised_reward_cents)
                    if t.advertised_reward_cents
                    else int(t.reward_funds_cents)
                ),
                "reward_prestige_delta": (
                    float(t.reward_prestige_delta) if t.reward_prestige_delta else 0.0
                ),
                "status": (
                    t.status.value if hasattr(t.status, "value") else str(t.status)
                ),
                "accepted_at": t.accepted_at.isoformat() if t.accepted_at else None,
                "deadline": t.deadline.isoformat() if t.deadline else None,
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                "domains": domains,
                "success": t.success,
            }
        )

    return result


def _extract_employees(db, company_id: UUID) -> List[Dict[str, Any]]:
    """Employee snapshot: current salary, tier, skill rates per domain."""
    from ..db.models.employee import Employee, EmployeeSkillRate

    employees = db.query(Employee).filter(Employee.company_id == company_id).all()
    result = []
    for emp in employees:
        skills = (
            db.query(EmployeeSkillRate)
            .filter(EmployeeSkillRate.employee_id == emp.id)
            .all()
        )
        skill_rates = {
            (s.domain.value if hasattr(s.domain, "value") else str(s.domain)): round(
                float(s.rate_domain_per_hour), 4
            )
            for s in skills
        }
        result.append(
            {
                "name": emp.name,
                "tier": emp.tier,
                "salary_cents": int(emp.salary_cents),
                "skill_rates": skill_rates,
            }
        )
    return result


def _extract_assignments(db, company_id: UUID) -> List[Dict[str, Any]]:
    """Per-task assignment details: which employees were assigned to each task."""
    from ..db.models.task import Task, TaskAssignment, TaskStatus
    from ..db.models.employee import Employee

    tasks = (
        db.query(Task)
        .filter(
            Task.company_id == company_id,
            Task.status != TaskStatus.MARKET,
        )
        .order_by(Task.accepted_at)
        .all()
    )

    result = []
    for t in tasks:
        assignments = (
            db.query(TaskAssignment).filter(TaskAssignment.task_id == t.id).all()
        )
        emp_names = []
        for a in assignments:
            emp = db.query(Employee).filter(Employee.id == a.employee_id).one_or_none()
            if emp:
                emp_names.append(emp.name)
        result.append(
            {
                "task_title": t.title,
                "status": (
                    t.status.value if hasattr(t.status, "value") else str(t.status)
                ),
                "employees_assigned": emp_names,
                "num_assigned": len(emp_names),
                "accepted_at": t.accepted_at.isoformat() if t.accepted_at else None,
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                "success": t.success,
            }
        )
    return result


def _extract_clients(db, company_id: UUID) -> List[Dict[str, Any]]:
    """Client info: name, loyalty, specialty domains, final trust level."""
    from ..db.models.client import Client, ClientTrust

    trust_rows = (
        db.query(ClientTrust, Client)
        .join(Client, Client.id == ClientTrust.client_id)
        .filter(ClientTrust.company_id == company_id)
        .all()
    )

    result = []
    for ct, client in trust_rows:
        result.append(
            {
                "name": client.name,
                "loyalty": round(float(client.loyalty), 4),
                "is_rat": client.loyalty < -0.3,
                "tier": client.tier,
                "specialty_domains": client.specialty_domains or [],
                "final_trust": round(float(ct.trust_level), 4),
            }
        )
    return result


def _extract_scratchpad(db, company_id: UUID) -> str | None:
    """Final scratchpad content."""
    from ..db.models.scratchpad import Scratchpad

    sp = db.query(Scratchpad).filter(Scratchpad.company_id == company_id).one_or_none()
    return sp.content if sp and sp.content else None


def _extract_config() -> Dict[str, Any]:
    """Snapshot of active world config for reproducibility."""
    from ..config import get_world_config

    wc = get_world_config()
    return {
        "num_employees": wc.num_employees,
        "num_clients": wc.num_clients,
        "initial_funds_cents": wc.initial_funds_cents,
        "salary_bump_pct": wc.salary_bump_pct,
        "trust_build_rate": wc.trust_build_rate,
        "trust_gating_fraction": wc.trust_gating_fraction,
        "trust_gated_reward_boost": wc.trust_gated_reward_boost,
        "trust_work_reduction_max": wc.trust_work_reduction_max,
        "loyalty_rat_fraction": wc.loyalty_rat_fraction,
        "loyalty_severity": wc.loyalty_severity,
        "penalty_fail_funds_pct": wc.penalty_fail_funds_pct,
        "deadline_min_biz_days": wc.deadline_min_biz_days,
        "deadline_qty_per_day": wc.deadline_qty_per_day,
        "prestige_decay_per_day": wc.prestige_decay_per_day,
        "reward_prestige_scale": wc.reward_prestige_scale,
        "skill_rate_max": wc.skill_rate_max,
        "market_browse_default_limit": wc.market_browse_default_limit,
    }
