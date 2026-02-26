"""Benchmark entrypoint: provisions DB, seeds world, runs agent loop to completion."""
from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from ..agent.commands.executor import run_command
from ..agent.loop import run_agent_loop
from ..agent.run_state import RunState
from ..agent.runtime.factory import build_runtime
from ..agent.runtime.schemas import RuntimeSettings
from ..db.session import build_engine, build_session_factory, session_scope, init_db
from .args import parse_run_args

logger = logging.getLogger(__name__)


def _parse_date(date_str: str) -> datetime:
    """Accept ISO (2025-01-01) or legacy MM/DD/YYYY format."""
    fmt = "%Y-%m-%d" if "-" in date_str else "%m/%d/%Y"
    dt = datetime.strptime(date_str, fmt)
    return dt.replace(hour=9, minute=0, second=0, tzinfo=timezone.utc)


def _wipe_simulation(db) -> None:
    """Delete all simulation rows so the DB can be reseeded cleanly."""
    from ..db.models.ledger import LedgerEntry
    from ..db.models.task import Task, TaskAssignment, TaskRequirement
    from ..db.models.employee import Employee, EmployeeSkillRate
    from ..db.models.company import Company, CompanyPrestige
    from ..db.models.sim_state import SimState
    from ..db.models.event import SimEvent
    from ..db.models.scratchpad import Scratchpad

    db.query(Scratchpad).delete(synchronize_session=False)
    db.query(LedgerEntry).delete(synchronize_session=False)
    db.query(TaskAssignment).delete(synchronize_session=False)
    db.query(TaskRequirement).delete(synchronize_session=False)
    db.query(Task).delete(synchronize_session=False)
    db.query(SimEvent).delete(synchronize_session=False)
    db.query(EmployeeSkillRate).delete(synchronize_session=False)
    db.query(Employee).delete(synchronize_session=False)
    db.query(CompanyPrestige).delete(synchronize_session=False)
    db.query(Company).delete(synchronize_session=False)
    db.query(SimState).delete(synchronize_session=False)
    db.flush()


def _init_simulation(db_factory, args, experiment_cfg, horizon_years):
    """Seed world directly (no CLI round-trip) using the experiment WorldConfig."""
    from ..db.models.event import EventType
    from ..db.models.sim_state import SimState
    from ..db.models.company import Company
    from ..core.events import insert_event
    from ..services.seed_world import SeedWorldRequest, seed_world_transactional

    with db_factory() as db:
        existing = db.query(SimState).first()
        if existing is not None:
            company = db.query(Company).filter(Company.id == existing.company_id).first()
            bankrupt = company is not None and company.funds_cents < 0
            horizon_reached = existing.sim_time >= existing.horizon_end
            if bankrupt or horizon_reached:
                logger.info(
                    "Existing simulation is terminal (bankrupt=%s horizon_reached=%s) — reseeding.",
                    bankrupt, horizon_reached,
                )
                _wipe_simulation(db)
            else:
                logger.info("Resuming non-terminal simulation (company_id=%s, sim_time=%s).",
                            existing.company_id, existing.sim_time)
                return existing.company_id

        start_dt = _parse_date(args.start_date)
        horizon_end = start_dt.replace(year=start_dt.year + horizon_years)
        world = experiment_cfg.world

        req = SeedWorldRequest(
            run_seed=args.seed,
            company_name=args.company_name,
            horizon_years=horizon_years,
            employee_count=world.num_employees,
            market_task_count=world.num_market_tasks,
            start_date=start_dt,
            cfg=world,
        )
        logger.info(
            "Initializing simulation: seed=%d employees=%d tasks=%d horizon=%dy",
            args.seed, world.num_employees, world.num_market_tasks, horizon_years,
        )
        result = seed_world_transactional(db, req)

        insert_event(
            db=db,
            company_id=result.company_id,
            event_type=EventType.HORIZON_END,
            scheduled_at=horizon_end,
            payload={"reason": "horizon_end"},
            dedupe_key="horizon_end",
        )
        db.add(SimState(
            company_id=result.company_id,
            sim_time=start_dt,
            run_seed=args.seed,
            horizon_end=horizon_end,
            replenish_counter=0,
        ))
        db.flush()

        logger.info("Simulation initialized: company_id=%s", result.company_id)
        return result.company_id


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_benchmark(args):
    """Run a full benchmark: migrate, seed, loop until terminal."""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(name)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Load experiment config — preset name or path to a .toml file
    from yc_bench.config import load_config
    experiment_cfg = load_config(args.config_name)
    # Propagate config name to CLI subprocess calls (e.g. task accept → generate_replacement_task)
    os.environ["YC_BENCH_EXPERIMENT"] = args.config_name
    # CLI --model always overrides the experiment's agent model
    experiment_cfg = experiment_cfg.model_copy(
        update={"agent": experiment_cfg.agent.model_copy(update={"model": args.model})}
    )
    # --horizon-years CLI flag overrides config; fall back to sim.horizon_years from config
    horizon_years = args.horizon_years if args.horizon_years is not None else experiment_cfg.sim.horizon_years

    logger.info(
        "YC-Bench starting: experiment=%s model=%s seed=%d horizon=%dy",
        experiment_cfg.name, args.model, args.seed, horizon_years,
    )

    # 1. Build engine and create all tables
    # If DATABASE_URL is not explicitly set, default to db/<config>_<seed>_<slug>.db
    if not os.environ.get("DATABASE_URL"):
        slug = args.model.replace("/", "_")
        db_dir = Path("db")
        db_dir.mkdir(exist_ok=True)
        os.environ["DATABASE_URL"] = f"sqlite:///{db_dir}/{args.config_name}_{args.seed}_{slug}.db"

    engine = build_engine()
    init_db(engine)
    factory = build_session_factory(engine)

    @contextmanager
    def db_factory():
        with session_scope(factory) as session:
            yield session

    # 3. Init simulation using experiment world config
    company_id = _init_simulation(db_factory, args, experiment_cfg, horizon_years)

    # 4. Build runtime settings from experiment AgentConfig
    agent_cfg = experiment_cfg.agent
    settings = RuntimeSettings(
        model=agent_cfg.model,
        temperature=agent_cfg.temperature,
        top_p=agent_cfg.top_p,
        request_timeout_seconds=agent_cfg.request_timeout_seconds,
        retry_max_attempts=agent_cfg.retry_max_attempts,
        retry_backoff_seconds=agent_cfg.retry_backoff_seconds,
        history_keep_rounds=agent_cfg.history_keep_rounds,
        system_prompt=agent_cfg.system_prompt,
    )
    runtime = build_runtime(settings, command_executor=run_command)

    # 5. Build run state
    session_id = f"run-{args.seed}-{args.model}"
    run_state = RunState(
        session_id=session_id,
        seed=args.seed,
        model=args.model,
        horizon_years=horizon_years,
    )

    # 6. Run agent loop
    loop_cfg = experiment_cfg.loop
    final_state = run_agent_loop(
        runtime=runtime,
        db_factory=db_factory,
        company_id=company_id,
        run_state=run_state,
        command_executor=run_command,
        auto_advance_after_turns=loop_cfg.auto_advance_after_turns,
        max_turns=loop_cfg.max_turns,
    )

    # 7. Save full rollout (with transcript) and print summary
    rollout = final_state.full_rollout()
    summary = final_state.summary()
    logger.info("Run complete: %s", json.dumps(summary, indent=2))

    # Write full rollout (includes transcript with commands)
    slug = args.model.replace("/", "_")
    results_dir = Path("results")
    results_dir.mkdir(exist_ok=True)
    results_path = results_dir / f"yc_bench_result_{args.config_name}_{args.seed}_{slug}.json"
    results_path.write_text(json.dumps(rollout, indent=2))
    logger.info("Full rollout written to %s (%d turns)", results_path, len(rollout.get("transcript", [])))

    return 0 if final_state.terminal_reason != "error" else 1


def main(argv=None):
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(usecwd=True), override=False)  # searches cwd upward for .env
    args = parse_run_args(argv)
    return run_benchmark(args)


if __name__ == "__main__":
    raise SystemExit(main())
