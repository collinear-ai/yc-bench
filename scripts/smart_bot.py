"""Smart bot: domain-focus prestige climbing with harvest phase.

Strategy:
  1. Pick 2 domains with most accessible tasks
  2. Only accept tasks whose domains are ALL within the focused pair
  3. Sort by prestige delta (climb fast)
  4. When no matching tasks exist, rotate to a new pair sharing 1 domain
  5. Once both focused domains reach prestige 3, switch to max reward

Uses bot_runner's candidate construction, but keeps its own run loop so it
can persist strategy state without modifying bot_runner.py.

Usage:
  uv run python scripts/smart_bot.py --config hard --seed 1
  uv run python scripts/smart_bot.py --config hard --seed 42 --seed 19 --seed 64
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bot_runner import _build_candidates


HARVEST_PRESTIGE_THRESHOLD = 3.0
MAX_CONCURRENT_TASKS = 1


def _pick_best_pair(candidates, must_include=None):
    """Pick the 2-domain pair covering the most candidates.
    If must_include is set, one domain must be in that set."""
    all_domains = sorted(set(str(d) for c in candidates for d in c.domains))
    if len(all_domains) < 2:
        return set(all_domains)
    pair_counts = Counter()
    for c in candidates:
        task_domains = set(str(d) for d in c.domains)
        for pair in combinations(all_domains, 2):
            if task_domains.issubset(set(pair)):
                if must_include is None or any(d in must_include for d in pair):
                    pair_counts[pair] += 1
    if pair_counts:
        return set(pair_counts.most_common(1)[0][0])
    return set(all_domains[:2])


def strategy_smart(candidates, context):
    """Domain-focus + prestige climb + harvest."""
    if not candidates:
        return None

    # Pick initial focused domains
    if "focused_domains" not in context:
        context["focused_domains"] = _pick_best_pair(candidates)
        context["harvest_mode"] = False

    focused = context["focused_domains"]

    # Filter to tasks in focused domains
    matching = [c for c in candidates if all(str(d) in focused for d in c.domains)]

    # Rotate if empty: pick new pair sharing 1 domain with current
    if not matching:
        new_focus = _pick_best_pair(candidates, must_include=focused)
        if new_focus != focused:
            context["focused_domains"] = new_focus
            focused = new_focus
            matching = [c for c in candidates if all(str(d) in focused for d in c.domains)]

    # If still empty after rotation, take any task
    if not matching:
        matching = candidates

    # Once both focused domains hit prestige 3, always harvest by reward.
    # Before that, alternate: odd turns = prestige delta, even turns = reward.
    # This balances prestige growth with cash flow.
    if not context["harvest_mode"] and "prestige_map" in context:
        p_map = context["prestige_map"]
        if all(
            any(float(v) >= HARVEST_PRESTIGE_THRESHOLD for k, v in p_map.items() if str(k) == d)
            for d in focused
        ):
            context["harvest_mode"] = True

    if context["harvest_mode"]:
        return max(matching, key=lambda c: c.reward_cents)
    else:
        turn = context.get("turn", 0)
        if turn % 3 == 0:
            # Every 3rd turn: pick highest reward (cash flow)
            return max(matching, key=lambda c: c.reward_cents)
        else:
            # Other turns: pick highest prestige delta (climb)
            return max(matching, key=lambda c: (c.prestige_delta, -c.completion_hours))


def main():
    parser = argparse.ArgumentParser(description="Smart bot: domain-focus + prestige climb + harvest")
    parser.add_argument("--config", default="hard")
    parser.add_argument("--seed", type=int, action="append", required=True)
    args = parser.parse_args()
    from decimal import Decimal
    from datetime import datetime, timezone
    from uuid import uuid4

    from yc_bench.config import load_config
    from yc_bench.core.business_time import add_business_hours
    from yc_bench.core.engine import advance_time
    from yc_bench.core.eta import recalculate_etas
    from yc_bench.core.events import fetch_next_event, insert_event
    from yc_bench.db.models.company import Company, CompanyPrestige
    from yc_bench.db.models.employee import Employee
    from yc_bench.db.models.event import EventType
    from yc_bench.db.models.sim_state import SimState
    from yc_bench.db.models.task import Task, TaskAssignment, TaskRequirement, TaskStatus
    from yc_bench.db.session import build_engine, build_session_factory, init_db, session_scope
    from yc_bench.services.generate_tasks import generate_replacement_task
    from yc_bench.services.seed_world import SeedWorldRequest, seed_world_transactional

    def _compute_deadline(accepted_at, max_domain_qty, world_cfg):
        work_hours = world_cfg.workday_end_hour - world_cfg.workday_start_hour
        biz_days = max(world_cfg.deadline_min_biz_days, int(max_domain_qty / world_cfg.deadline_qty_per_day))
        return add_business_hours(accepted_at, Decimal(str(biz_days)) * Decimal(str(work_hours)))

    results = []
    for seed in args.seed:
        config_name = args.config
        cfg = load_config(config_name)
        world_cfg = cfg.world
        bot_slug = "smart_bot"

        db_dir = Path("db")
        db_dir.mkdir(exist_ok=True)
        db_path = db_dir / f"{config_name}_{seed}_{bot_slug}.db"
        if db_path.exists():
            db_path.unlink()

        db_url = f"sqlite:///{db_path}"
        os.environ["DATABASE_URL"] = db_url
        os.environ["YC_BENCH_EXPERIMENT"] = config_name

        engine = build_engine(db_url)
        init_db(engine)
        factory = build_session_factory(engine)

        with session_scope(factory) as db:
            start_dt = datetime(2025, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
            horizon_end = start_dt.replace(year=start_dt.year + cfg.sim.horizon_years)

            req = SeedWorldRequest(
                run_seed=seed,
                company_name="Smart Bot",
                horizon_years=cfg.sim.horizon_years,
                employee_count=world_cfg.num_employees,
                market_task_count=world_cfg.num_market_tasks,
                cfg=world_cfg,
                start_date=start_dt,
            )
            result = seed_world_transactional(db, req)
            company_id = result.company_id

            insert_event(
                db=db, company_id=company_id,
                event_type=EventType.HORIZON_END,
                scheduled_at=horizon_end,
                payload={"reason": "horizon_end"},
                dedupe_key="horizon_end",
            )
            db.add(SimState(
                company_id=company_id, sim_time=start_dt,
                run_seed=seed, horizon_end=horizon_end, replenish_counter=0,
            ))
            db.flush()

        tasks_completed = 0
        tasks_failed = 0
        turn = 0
        strategy_context = {}  # persistent across turns

        while True:
            turn += 1

            with session_scope(factory) as db:
                sim_state = db.query(SimState).first()
                company = db.query(Company).filter(Company.id == company_id).one()

                if company.funds_cents < 0:
                    break
                if sim_state.sim_time >= sim_state.horizon_end:
                    break

                active_count = db.query(Task).filter(
                    Task.company_id == company_id,
                    Task.status == TaskStatus.ACTIVE,
                ).count()

                newly_accepted = []
                while active_count + len(newly_accepted) < MAX_CONCURRENT_TASKS and len(newly_accepted) < 1:
                    employees = db.query(Employee).filter(Employee.company_id == company_id).all()
                    employee_tiers = [emp.tier for emp in employees]
                    employee_ids = [emp.id for emp in employees]

                    n_will_be_active = active_count + len(newly_accepted)
                    candidates, max_prestige = _build_candidates(
                        db, company_id, sim_state, world_cfg, employee_tiers,
                        n_active=n_will_be_active,
                    )

                    # Build prestige map for strategy
                    prestige_rows = db.query(CompanyPrestige).filter(
                        CompanyPrestige.company_id == company_id).all()
                    prestige_map = {p.domain: float(p.prestige_level) for p in prestige_rows}

                    strategy_context.update({
                        "seed": seed,
                        "turn": turn + len(newly_accepted),
                        "max_prestige": max_prestige,
                        "prestige_map": prestige_map,
                    })

                    # Add domain info to candidates (bot_runner doesn't include it)
                    for c in candidates:
                        reqs = db.query(TaskRequirement).filter(
                            TaskRequirement.task_id == c.task.id).all()
                        c.domains = [r.domain for r in reqs]

                    chosen = strategy_smart(candidates, strategy_context)
                    if chosen is None:
                        break

                    task = chosen.task
                    newly_accepted.append(task.id)

                    reqs = db.query(TaskRequirement).filter(
                        TaskRequirement.task_id == task.id).all()

                    # Apply trust work reduction
                    if task.client_id is not None:
                        from yc_bench.db.models.client import ClientTrust
                        ct = db.query(ClientTrust).filter(
                            ClientTrust.company_id == company_id,
                            ClientTrust.client_id == task.client_id,
                        ).one_or_none()
                        trust_level = float(ct.trust_level) if ct else 0.0
                        work_reduction = world_cfg.trust_work_reduction_max * (trust_level / world_cfg.trust_max)
                        for r in reqs:
                            r.required_qty = int(float(r.required_qty) * (1 - work_reduction))

                    max_domain_qty = max(float(r.required_qty) for r in reqs)

                    task.status = TaskStatus.PLANNED
                    task.company_id = company_id
                    task.accepted_at = sim_state.sim_time
                    task.deadline = _compute_deadline(sim_state.sim_time, max_domain_qty, world_cfg)

                    # Generate replacement
                    counter = sim_state.replenish_counter
                    sim_state.replenish_counter = counter + 1

                    from yc_bench.db.models.client import Client as ClientModel
                    replaced_client_index = 0
                    if task.client_id is not None:
                        clients = db.query(ClientModel).order_by(ClientModel.name).all()
                        for i, c in enumerate(clients):
                            if c.id == task.client_id:
                                replaced_client_index = i
                                break

                    replacement_spec_domains = None
                    if task.client_id is not None:
                        orig_client = db.query(ClientModel).filter(ClientModel.id == task.client_id).one_or_none()
                        if orig_client:
                            replacement_spec_domains = orig_client.specialty_domains

                    replacement = generate_replacement_task(
                        run_seed=sim_state.run_seed,
                        replenish_counter=counter,
                        replaced_prestige=task.required_prestige,
                        replaced_client_index=replaced_client_index,
                        cfg=world_cfg,
                        specialty_domains=replacement_spec_domains,
                    )

                    clients = db.query(ClientModel).order_by(ClientModel.name).all()
                    replacement_client = clients[replacement.client_index % len(clients)] if clients else None
                    replacement_client_id = replacement_client.id if replacement_client else None

                    replacement_row = Task(
                        id=uuid4(),
                        company_id=None,
                        client_id=replacement_client_id,
                        status=TaskStatus.MARKET,
                        title=replacement.title,
                        required_prestige=replacement.required_prestige,
                        reward_funds_cents=replacement.reward_funds_cents,
                        reward_prestige_delta=replacement.reward_prestige_delta,
                        skill_boost_pct=replacement.skill_boost_pct,
                        accepted_at=None, deadline=None, completed_at=None,
                        success=None, progress_milestone_pct=0,
                        required_trust=replacement.required_trust,
                    )
                    db.add(replacement_row)
                    for domain, qty in replacement.requirements.items():
                        db.add(TaskRequirement(
                            task_id=replacement_row.id,
                            domain=domain,
                            required_qty=qty,
                            completed_qty=0,
                        ))

                    for eid in employee_ids:
                        db.add(TaskAssignment(
                            task_id=task.id,
                            employee_id=eid,
                            assigned_at=sim_state.sim_time,
                        ))
                    db.flush()

                    task.status = TaskStatus.ACTIVE
                    db.flush()

                if newly_accepted:
                    recalculate_etas(db, company_id, sim_state.sim_time,
                                     impacted_task_ids=set(newly_accepted),
                                     milestones=world_cfg.task_progress_milestones)

                total_active = active_count + len(newly_accepted)
                if total_active == 0:
                    next_event = fetch_next_event(db, company_id, sim_state.horizon_end)
                    if next_event is None:
                        break
                    adv = advance_time(db, company_id, next_event.scheduled_at)
                    if adv.bankrupt or adv.horizon_reached:
                        break
                    continue

                next_event = fetch_next_event(db, company_id, sim_state.horizon_end)
                if next_event is None:
                    break
                adv = advance_time(db, company_id, next_event.scheduled_at)
                for we in adv.wake_events:
                    if we.get("type") == "task_completed":
                        if we.get("success"):
                            tasks_completed += 1
                        else:
                            tasks_failed += 1
                if adv.bankrupt or adv.horizon_reached:
                    break

        # Final state
        with session_scope(factory) as db:
            company = db.query(Company).filter(Company.id == company_id).one()
            final_balance = company.funds_cents
            bankrupt = final_balance < 0
            prestige_rows = db.query(CompanyPrestige).filter(
                CompanyPrestige.company_id == company_id).all()
            max_p = max((float(p.prestige_level) for p in prestige_rows), default=1.0)

        status = "BANKRUPT" if bankrupt else f"${final_balance / 100:,.0f}"
        print(f"  smart_bot | {config_name} seed={seed} ... {status} | {tasks_completed} OK, {tasks_failed} fail | prestige {max_p:.1f} | {turn} turns")
        results.append({
            "seed": seed, "final_balance": final_balance, "bankrupt": bankrupt,
            "tasks_ok": tasks_completed, "tasks_fail": tasks_failed, "prestige": max_p,
        })

    print()
    print(f"{'Bot':24s} {'Config':12s} {'Seed':>6s} {'Final Balance':>15s} {'OK':>4s} {'Fail':>5s} {'Prestige':>10s}")
    print("-" * 80)
    for r in results:
        status = "BANKRUPT" if r["bankrupt"] else f"${r['final_balance'] / 100:,.0f}"
        print(f"{'smart_bot':24s} {config_name:12s} {r['seed']:>6d} {status:>15s} {r['tasks_ok']:>4d} {r['tasks_fail']:>5d} {r['prestige']:>10.1f}")
    bankruptcies = sum(1 for r in results if r["bankrupt"])
    print(f"\nBankruptcies: {bankruptcies}/{len(results)}")


if __name__ == "__main__":
    main()
