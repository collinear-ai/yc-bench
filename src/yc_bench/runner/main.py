"""Benchmark entrypoint: provisions DB, seeds world, runs agent loop to completion."""
from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from ..agent.commands.executor import run_command
from ..agent.loop import run_agent_loop
from ..agent.run_state import RunState, TerminalReason
from ..agent.runtime.factory import build_runtime
from ..agent.runtime.schemas import RuntimeSettings
from ..db.session import build_engine, build_session_factory, session_scope, init_db
from .args import parse_run_args
from .extract import extract_time_series

logger = logging.getLogger(__name__)

# Loggers that produce noisy debug output during LLM calls
_NOISY_LOGGERS = ("litellm", "httpx", "httpcore", "openai", "LiteLLM")


def _parse_date(date_str: str) -> datetime:
    """Accept ISO (2025-01-01) or legacy MM/DD/YYYY format."""
    fmt = "%Y-%m-%d" if "-" in date_str else "%m/%d/%Y"
    dt = datetime.strptime(date_str, fmt)
    return dt.replace(hour=9, minute=0, second=0, tzinfo=timezone.utc)


def _wipe_simulation(db) -> None:
    """Delete all simulation rows so the DB can be reseeded cleanly."""
    from ..db.models.client import Client, ClientTrust
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
    db.query(ClientTrust).delete(synchronize_session=False)
    db.query(Client).delete(synchronize_session=False)
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

def _redirect_all_logging_to_file(log_file: Path) -> None:
    """Redirect ALL logging from the console to a file.

    When the Rich Live dashboard is active, any output to stdout/stderr
    breaks the in-place rendering, causing stacked panels. This removes
    the root logger's console handlers and replaces them with a file handler.
    """
    log_file.parent.mkdir(exist_ok=True)
    file_handler = logging.FileHandler(str(log_file), mode="a")
    file_handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(name)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    # Replace all console handlers on root logger with the file handler
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(file_handler)

    # Also ensure noisy loggers don't propagate (belt and suspenders)
    for name in _NOISY_LOGGERS:
        lg = logging.getLogger(name)
        lg.propagate = False
        lg.handlers.clear()
        lg.addHandler(file_handler)


def _build_db_url(args, episode: int, max_episodes: int) -> str:
    """Build SQLite DATABASE_URL, adding episode suffix when multi-episode."""
    slug = args.model.replace("/", "_")
    db_dir = Path("db")
    db_dir.mkdir(exist_ok=True)
    base = f"{args.config_name}_{args.seed}_{slug}"
    if max_episodes > 1:
        return f"sqlite:///{db_dir}/{base}.ep{episode}.db"
    return f"sqlite:///{db_dir}/{base}.db"


def _read_scratchpad(db_factory, company_id) -> str:
    """Read scratchpad content from the current DB."""
    from ..db.models.scratchpad import Scratchpad
    with db_factory() as db:
        row = db.query(Scratchpad).filter(Scratchpad.company_id == company_id).first()
        return row.content if row else ""


def _write_scratchpad(db_factory, company_id, content: str) -> None:
    """Write scratchpad content into the current DB (upsert)."""
    from ..db.models.scratchpad import Scratchpad
    with db_factory() as db:
        row = db.query(Scratchpad).filter(Scratchpad.company_id == company_id).first()
        if row is None:
            db.add(Scratchpad(company_id=company_id, content=content))
        else:
            row.content = content
        db.flush()


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

    # Decide whether to use the live dashboard
    use_live = sys.stdout.isatty() and not getattr(args, "no_live", False)

    # When using the live dashboard, redirect all logging to file immediately
    # so no console output interferes with Rich Live rendering.
    if use_live:
        log_file = Path("logs") / "debug.log"
        _redirect_all_logging_to_file(log_file)

    logger.info(
        "YC-Bench starting: experiment=%s model=%s seed=%d horizon=%dy max_episodes=%d",
        experiment_cfg.name, args.model, args.seed, horizon_years, args.max_episodes,
    )

    # Build runtime settings (shared across episodes)
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

    # Build run state (persists across episodes)
    session_id = f"run-{args.seed}-{args.model}"
    run_state = RunState(
        session_id=session_id,
        seed=args.seed,
        model=args.model,
        horizon_years=horizon_years,
    )

    loop_cfg = experiment_cfg.loop
    max_episodes = args.max_episodes
    carried_scratchpad = ""

    for episode in range(1, max_episodes + 1):
        logger.info("=== Episode %d / %d ===", episode, max_episodes)

        # 1. Build engine for this episode's DB
        db_url = _build_db_url(args, episode, max_episodes)
        os.environ["DATABASE_URL"] = db_url
        engine = build_engine()
        init_db(engine)
        factory = build_session_factory(engine)

        @contextmanager
        def db_factory(_factory=factory):
            with session_scope(_factory) as session:
                yield session

        # 2. Init simulation
        company_id = _init_simulation(db_factory, args, experiment_cfg, horizon_years)

        # 3. Restore scratchpad from previous episode
        if episode > 1 and carried_scratchpad:
            _write_scratchpad(db_factory, company_id, carried_scratchpad)
            logger.info("Restored scratchpad from episode %d (%d chars).", episode - 1, len(carried_scratchpad))

        # 4. Set up live dashboard + live transcript file
        dashboard = None
        on_turn_start = None
        on_turn = None

        # Write live transcript alongside the DB so the streamlit dashboard can read it
        _slug = args.model.replace("/", "_")
        transcript_path = Path("db") / f"{args.config_name}_{args.seed}_{_slug}.transcript.jsonl"
        if transcript_path.exists():
            transcript_path.unlink()

        def _write_live_transcript(snapshot, rs, commands):
            """Append one JSONL line per turn for the streamlit dashboard."""
            if not rs.transcript:
                return
            entry = rs.transcript[-1]
            import json as _json
            line = _json.dumps({
                "turn": entry.turn,
                "timestamp": entry.timestamp,
                "agent_output": entry.agent_output,
                "commands_executed": entry.commands_executed,
                "sim_time": snapshot.get("sim_time", ""),
                "funds_cents": snapshot.get("funds_cents", 0),
            }, separators=(",", ":"))
            with open(transcript_path, "a") as f:
                f.write(line + "\n")

        if use_live:
            from .dashboard import BenchmarkDashboard

            dashboard = BenchmarkDashboard(
                model=args.model,
                seed=args.seed,
                config_name=args.config_name,
                db_factory=db_factory,
                company_id=company_id,
            )

            def on_turn_start(turn_num):
                dashboard.mark_turn_start(turn_num)

            def on_turn(snapshot, rs, commands):
                dashboard.update(snapshot, rs, commands)
                _write_live_transcript(snapshot, rs, commands)
        else:
            def on_turn(snapshot, rs, commands):
                _write_live_transcript(snapshot, rs, commands)

        # 5. Run agent loop for this episode
        try:
            if dashboard is not None:
                dashboard.start()

            final_state = run_agent_loop(
                runtime=runtime,
                db_factory=db_factory,
                company_id=company_id,
                run_state=run_state,
                command_executor=run_command,
                auto_advance_after_turns=loop_cfg.auto_advance_after_turns,
                max_turns=loop_cfg.max_turns,
                on_turn_start=on_turn_start,
                on_turn=on_turn,
                episode=episode,
            )
        finally:
            if dashboard is not None:
                dashboard.stop()

        if dashboard is not None:
            dashboard.print_final_summary(final_state)

        # 6. For multi-episode runs, snapshot this episode's data
        if max_episodes > 1:
            run_state.finish_episode()

        logger.info("Episode %d finished: reason=%s", episode, run_state.terminal_reason)

        # 7. If not bankruptcy, or last episode, stop
        if run_state.terminal_reason != TerminalReason.BANKRUPTCY or episode == max_episodes:
            break

        # 8. Save scratchpad for next episode, then reset
        carried_scratchpad = _read_scratchpad(db_factory, company_id)
        logger.info("Carrying scratchpad to episode %d (%d chars).", episode + 1, len(carried_scratchpad))

        # Clear runtime session (fresh conversation history)
        runtime.clear_session(session_id)
        run_state.reset_for_new_episode()
        engine.dispose()

    # 9. Save full rollout and print summary
    rollout = final_state.full_rollout()
    rollout["time_series"] = extract_time_series(db_factory, company_id)
    summary = final_state.summary()
    logger.info("Run complete: %s", json.dumps(summary, indent=2))

    slug = args.model.replace("/", "_")
    results_dir = Path("results")
    results_dir.mkdir(exist_ok=True)
    results_path = results_dir / f"yc_bench_result_{args.config_name}_{args.seed}_{slug}.json"
    results_path.write_text(json.dumps(rollout, indent=2))
    logger.info("Full rollout written to %s", results_path)

    return 0 if final_state.terminal_reason != "error" else 1


def main(argv=None):
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(usecwd=True), override=False)  # searches cwd upward for .env
    args = parse_run_args(argv)
    return run_benchmark(args)


if __name__ == "__main__":
    raise SystemExit(main())
