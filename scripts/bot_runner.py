"""Bot runner: plays YC-Bench using direct DB access with pluggable strategies.

Strategies:
  greedy     — pick highest reward among completable tasks
  random     — pick randomly among completable tasks (deterministic via RngStreams)
  throughput — pick highest reward/hour among completable tasks
  prestige   — phase 1: climb prestige fast, phase 2: throughput

Usage:
  uv run python scripts/bot_runner.py                    # all bots, all configs, all seeds
  uv run python scripts/bot_runner.py --bot greedy       # just greedy
  uv run python scripts/bot_runner.py --bot random --seed 1 --config medium
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from yc_bench.config import load_config
from yc_bench.core.business_time import add_business_hours
from yc_bench.core.engine import advance_time
from yc_bench.core.eta import recalculate_etas
from yc_bench.core.events import fetch_next_event, insert_event
from yc_bench.db.models.company import Company, CompanyPrestige
from yc_bench.db.models.employee import Employee, EmployeeSkillRate
from yc_bench.db.models.event import EventType
from yc_bench.db.models.sim_state import SimState
from yc_bench.db.models.task import Task, TaskAssignment, TaskRequirement, TaskStatus
from yc_bench.db.session import build_engine, build_session_factory, init_db, session_scope
from yc_bench.services.generate_tasks import generate_replacement_task
from yc_bench.services.rng import RngStreams
from yc_bench.services.seed_world import SeedWorldRequest, seed_world_transactional

CONFIGS = ["medium", "hard", "nightmare"]
SEEDS = [1, 2, 3]

MAX_TASK_CYCLES = None  # No cap — bot plays until horizon end


@dataclass
class CandidateTask:
    task: object  # ORM Task row
    reward_cents: int
    prestige_delta: float
    completion_hours: Decimal
    is_completable: bool


# Tier-average rates: E[uniform(0, max_rate)] = max_rate / 2.
# The LLM agent only sees tier + salary, not actual per-domain rates.
_TIER_AVG_RATE = {
    "junior": Decimal("2.0"),   # uniform(0, 4) => E=2.0
    "mid": Decimal("3.5"),      # uniform(0, 7) => E=3.5
    "senior": Decimal("5.0"),   # uniform(0, 10) => E=5.0
}


def estimate_completion_hours(task_reqs, employee_tiers, n_concurrent_tasks=1):
    """Estimate hours to complete task using tier-average rates (blind to actual skills).

    employee_tiers is a list of tier strings like ["junior", "mid", "senior", ...].
    Each employee is assumed to contribute their tier's average rate to every domain.
    """
    total_rate = sum(_TIER_AVG_RATE[t] for t in employee_tiers)
    effective_rate = total_rate / Decimal(n_concurrent_tasks)

    if effective_rate <= 0:
        return None

    max_hours = Decimal("0")
    for req in task_reqs:
        qty = Decimal(str(req["required_qty"]))
        hours = qty / effective_rate
        if hours > max_hours:
            max_hours = hours
    return max_hours


def _compute_deadline(accepted_at, max_domain_qty, cfg):
    work_hours = cfg.workday_end_hour - cfg.workday_start_hour
    biz_days = max(cfg.deadline_min_biz_days, int(max_domain_qty / cfg.deadline_qty_per_day))
    return add_business_hours(accepted_at, Decimal(str(biz_days)) * Decimal(str(work_hours)))


def _build_candidates(db, company_id, sim_state, world_cfg, employee_tiers):
    """Build CandidateTask list for all market tasks the company can accept (per-domain prestige gating).

    Uses tier-average rates (blind to actual per-domain skills) to mirror
    the information available to an LLM agent.
    """
    prestige_rows = db.query(CompanyPrestige).filter(
        CompanyPrestige.company_id == company_id
    ).all()
    prestige_map = {p.domain: float(p.prestige_level) for p in prestige_rows}
    min_prestige = min(prestige_map.values()) if prestige_map else 1.0

    market_tasks = db.query(Task).filter(
        Task.status == TaskStatus.MARKET,
    ).order_by(Task.reward_funds_cents.desc()).all()

    candidates = []
    for task in market_tasks:
        reqs = db.query(TaskRequirement).filter(
            TaskRequirement.task_id == task.id
        ).all()

        # Per-domain prestige check: all required domains must meet threshold
        meets_prestige = all(
            prestige_map.get(r.domain, 1.0) >= task.required_prestige
            for r in reqs
        )
        if not meets_prestige:
            continue

        max_domain_qty = max(float(r.required_qty) for r in reqs)
        task_reqs = [{"domain": r.domain, "required_qty": float(r.required_qty)} for r in reqs]

        completion_hours = estimate_completion_hours(task_reqs, employee_tiers, n_concurrent_tasks=1)

        is_completable = False
        if completion_hours is not None:
            deadline = _compute_deadline(sim_state.sim_time, max_domain_qty, world_cfg)
            completion_time = add_business_hours(sim_state.sim_time, completion_hours)
            is_completable = completion_time <= deadline

        candidates.append(CandidateTask(
            task=task,
            reward_cents=task.reward_funds_cents,
            prestige_delta=float(task.reward_prestige_delta),
            completion_hours=completion_hours if completion_hours is not None else Decimal("999999"),
            is_completable=is_completable,
        ))

    return candidates, min_prestige


# ── Strategy functions ──────────────────────────────────────────────────────

StrategyFn = Callable  # (completable: list[CandidateTask], context: dict) -> Optional[CandidateTask]


def strategy_greedy(completable: list[CandidateTask], context: dict) -> Optional[CandidateTask]:
    """Pick the task with the highest reward."""
    if not completable:
        return None
    return max(completable, key=lambda c: c.reward_cents)


def strategy_random(completable: list[CandidateTask], context: dict) -> Optional[CandidateTask]:
    """Pick a random completable task (deterministic via seeded RNG)."""
    if not completable:
        return None
    seed = context["seed"]
    turn = context["turn"]
    rng = RngStreams(seed).stream(f"bot_random_select:{turn}")
    return rng.choice(completable)


def strategy_throughput(completable: list[CandidateTask], context: dict) -> Optional[CandidateTask]:
    """Pick the task with the highest reward per hour."""
    if not completable:
        return None
    return max(completable, key=lambda c: Decimal(c.reward_cents) / c.completion_hours)


def strategy_prestige(completable: list[CandidateTask], context: dict) -> Optional[CandidateTask]:
    """Phase 1 (prestige < 5): climb prestige fastest. Phase 2: throughput."""
    if not completable:
        return None
    current_prestige = context["max_prestige"]
    if current_prestige < 5:
        # Prefer tasks that give prestige delta per hour of work
        prestige_tasks = [c for c in completable if c.prestige_delta > 0]
        if prestige_tasks:
            return max(prestige_tasks, key=lambda c: Decimal(str(c.prestige_delta)) / c.completion_hours)
    # Fall back to throughput
    return max(completable, key=lambda c: Decimal(c.reward_cents) / c.completion_hours)


STRATEGIES = {
    "greedy": ("greedy_bot", strategy_greedy),
    "random": ("random_bot", strategy_random),
    "throughput": ("throughput_bot", strategy_throughput),
    "prestige": ("prestige_bot", strategy_prestige),
}


# ── Shared simulation runner ───────────────────────────────────────────────

def run_bot(config_name: str, seed: int, bot_slug: str, strategy_fn: StrategyFn):
    """Run a bot strategy on one (config, seed) pair. Returns result dict."""
    cfg = load_config(config_name)
    world_cfg = cfg.world

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
            company_name=bot_slug.replace("_", " ").title(),
            horizon_years=cfg.sim.horizon_years,
            employee_count=world_cfg.num_employees,
            market_task_count=world_cfg.num_market_tasks,
            start_date=start_dt,
        )
        result = seed_world_transactional(db, req)
        company_id = result.company_id

        insert_event(
            db=db,
            company_id=company_id,
            event_type=EventType.HORIZON_END,
            scheduled_at=horizon_end,
            payload={"reason": "horizon_end"},
            dedupe_key="horizon_end",
        )

        sim_state = SimState(
            company_id=company_id,
            sim_time=start_dt,
            run_seed=seed,
            horizon_end=horizon_end,
            replenish_counter=0,
        )
        db.add(sim_state)
        db.flush()

    tasks_completed = 0
    tasks_failed = 0
    task_cycles_used = 0
    turn = 0

    while True:
        turn += 1

        with session_scope(factory) as db:
            sim_state = db.query(SimState).first()
            company = db.query(Company).filter(Company.id == company_id).one()

            if company.funds_cents < 0:
                break
            if sim_state.sim_time >= sim_state.horizon_end:
                break

            active_tasks = db.query(Task).filter(
                Task.company_id == company_id,
                Task.status == TaskStatus.ACTIVE,
            ).all()

            if active_tasks:
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
                continue

            # Get employees — only tier info (same as LLM agent sees)
            employees = db.query(Employee).filter(Employee.company_id == company_id).all()
            employee_tiers = [emp.tier for emp in employees]
            employee_ids = [emp.id for emp in employees]

            candidates, max_prestige = _build_candidates(db, company_id, sim_state, world_cfg, employee_tiers)
            completable = [c for c in candidates if c.is_completable]

            context = {
                "seed": seed,
                "turn": turn,
                "max_prestige": max_prestige,
            }
            chosen = strategy_fn(completable, context)

            if chosen is None:
                next_event = fetch_next_event(db, company_id, sim_state.horizon_end)
                if next_event is None:
                    adv = advance_time(db, company_id, sim_state.horizon_end)
                    break
                adv = advance_time(db, company_id, next_event.scheduled_at)
                if adv.bankrupt or adv.horizon_reached:
                    break
                continue

            best_task = chosen.task

            # Accept the task
            reqs = db.query(TaskRequirement).filter(
                TaskRequirement.task_id == best_task.id
            ).all()
            max_domain_qty = max(float(r.required_qty) for r in reqs)

            best_task.status = TaskStatus.PLANNED
            best_task.company_id = company_id
            best_task.accepted_at = sim_state.sim_time
            best_task.deadline = _compute_deadline(sim_state.sim_time, max_domain_qty, world_cfg)

            # Generate replacement
            counter = sim_state.replenish_counter
            sim_state.replenish_counter = counter + 1
            replacement = generate_replacement_task(
                run_seed=sim_state.run_seed,
                replenish_counter=counter,
                replaced_prestige=best_task.required_prestige,
                cfg=world_cfg,
            )
            replacement_row = Task(
                id=uuid4(),
                company_id=None,
                status=TaskStatus.MARKET,
                title=replacement.title,
                required_prestige=replacement.required_prestige,
                reward_funds_cents=replacement.reward_funds_cents,
                reward_prestige_delta=replacement.reward_prestige_delta,
                skill_boost_pct=replacement.skill_boost_pct,
                accepted_at=None, deadline=None, completed_at=None,
                success=None, progress_milestone_pct=0,
            )
            db.add(replacement_row)
            for domain, qty in replacement.requirements.items():
                db.add(TaskRequirement(
                    task_id=replacement_row.id,
                    domain=domain,
                    required_qty=qty,
                    completed_qty=0,
                ))

            # Assign ALL employees
            for eid in employee_ids:
                db.add(TaskAssignment(
                    task_id=best_task.id,
                    employee_id=eid,
                    assigned_at=sim_state.sim_time,
                ))
            db.flush()

            best_task.status = TaskStatus.ACTIVE
            db.flush()

            recalculate_etas(db, company_id, sim_state.sim_time,
                             impacted_task_ids={best_task.id},
                             milestones=world_cfg.task_progress_milestones)

            task_cycles_used += 1

    # Final state
    with session_scope(factory) as db:
        company = db.query(Company).filter(Company.id == company_id).one()
        sim_state = db.query(SimState).first()

        final_balance = company.funds_cents
        bankrupt = final_balance < 0

        prestige_rows = db.query(CompanyPrestige).filter(
            CompanyPrestige.company_id == company_id
        ).all()
        max_p = max((float(p.prestige_level) for p in prestige_rows), default=1.0)

    return {
        "config": config_name,
        "seed": seed,
        "bot": bot_slug,
        "turns": turn,
        "final_balance_cents": final_balance,
        "bankrupt": bankrupt,
        "tasks_completed": tasks_completed,
        "tasks_failed": tasks_failed,
        "max_prestige": max_p,
    }


def main():
    parser = argparse.ArgumentParser(description="Run YC-Bench bot strategies")
    parser.add_argument("--bot", choices=list(STRATEGIES.keys()), default=None,
                        help="Run only this bot (default: all)")
    parser.add_argument("--config", choices=CONFIGS, default=None,
                        help="Run only this config (default: all)")
    parser.add_argument("--seed", type=int, default=None,
                        help="Run only this seed (default: all)")
    args = parser.parse_args()

    bots = [args.bot] if args.bot else list(STRATEGIES.keys())
    configs = [args.config] if args.config else CONFIGS
    seeds = [args.seed] if args.seed else SEEDS

    results = []
    total = len(bots) * len(configs) * len(seeds)
    print(f"Running {total} bot simulations...\n")

    for bot_name in bots:
        slug, strategy_fn = STRATEGIES[bot_name]
        for config_name in configs:
            for seed in seeds:
                print(f"  {slug} | {config_name} seed={seed} ...", end=" ", flush=True)
                r = run_bot(config_name, seed, slug, strategy_fn)
                results.append(r)

                if r["bankrupt"]:
                    tag = "BANKRUPT"
                else:
                    tag = f"${r['final_balance_cents']/100:,.0f}"
                print(f"{tag} | {r['tasks_completed']} OK, {r['tasks_failed']} fail | prestige {r['max_prestige']:.1f} | {r['turns']} turns")

    print(f"\n{'Bot':<16} {'Config':<12} {'Seed':<5} {'Final Balance':>14} {'OK':>4} {'Fail':>5} {'Prestige':>9}")
    print("-" * 70)
    for r in results:
        fb = "BANKRUPT" if r["bankrupt"] else f"${r['final_balance_cents']/100:,.0f}"
        print(f"{r['bot']:<16} {r['config']:<12} {r['seed']:<5} {fb:>14} {r['tasks_completed']:>4} {r['tasks_failed']:>5} {r['max_prestige']:>8.1f}")

    bankrupt_count = sum(1 for r in results if r["bankrupt"])
    print(f"\nBankruptcies: {bankrupt_count}/{len(results)}")


if __name__ == "__main__":
    main()
