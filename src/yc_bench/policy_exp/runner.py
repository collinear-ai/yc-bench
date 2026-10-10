"""Per-(model, seed) policy execution: provision DB, seed world,
exec the generated policy, score the resulting state."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ..config import load_config
from ..db.session import build_engine, build_session_factory, init_db, session_scope
from ..runner.extract import extract_time_series
from ..runner.main import _init_simulation
from .api import PolicyBudgetExceeded, PolicyEnv, PolicyTerminated
from .codegen import load_policy

logger = logging.getLogger(__name__)


@dataclass
class EpisodeResult:
    model: str
    seed: int
    config_name: str
    policy_path: str
    final_funds_cents: int
    initial_funds_cents: int
    funds_delta_cents: int
    bankrupt: bool
    terminal_reason: str | None
    sim_commands: int
    helper_calls: int
    wall_seconds: float
    error: str | None = None
    time_series: dict[str, Any] = field(default_factory=dict)
    helper_log: list[dict] = field(default_factory=list)
    command_log_tail: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _build_db_url(config_name: str, model: str, seed: int, suffix: str = "") -> str:
    slug = model.replace("/", "_")
    db_dir = Path("db")
    db_dir.mkdir(exist_ok=True)
    suffix_str = f"_{suffix}" if suffix else ""
    return f"sqlite:///{db_dir}/policy_{config_name}_{seed}_{slug}{suffix_str}.db"


def _wipe_db_file(db_url: str) -> None:
    """Each run starts from a clean SQLite file so seeds are reproducible."""
    if not db_url.startswith("sqlite:///"):
        return
    path = Path(db_url[len("sqlite:///"):])
    if path.exists():
        path.unlink()


def _read_company_state(factory, company_id) -> dict[str, Any]:
    from ..db.models.company import Company

    with session_scope(factory) as db:
        c = db.query(Company).filter(Company.id == company_id).one()
        return {
            "funds_cents": int(c.funds_cents),
            "bankrupt": int(c.funds_cents) < 0,
        }


def _exec_with_timeout(fn, args, timeout_seconds: float) -> tuple[Exception | None, float]:
    """Run fn(*args) on a daemon thread; return (exception, wall_seconds).

    If the policy exceeds timeout, the thread is left to die with the
    process — there is no safe way to interrupt arbitrary user code in
    Python. The caller should treat timeout as terminal and move on.
    """
    box: dict[str, Any] = {"exc": None}

    def _target():
        try:
            fn(*args)
        except Exception as exc:  # noqa: BLE001
            box["exc"] = exc

    t = threading.Thread(target=_target, daemon=True)
    t0 = time.time()
    t.start()
    t.join(timeout=timeout_seconds)
    elapsed = time.time() - t0
    if t.is_alive():
        return TimeoutError(f"policy exceeded wall timeout {timeout_seconds}s"), elapsed
    return box["exc"], elapsed


def run_policy_episode(
    model: str,
    seed: int,
    policy_path: Path | str,
    *,
    config_name: str = "default",
    start_date: str = "2025-01-01",
    company_name: str = "BenchCo",
    horizon_years: int | None = None,
    wall_timeout_seconds: float = 1800.0,
    helper_model: str | None = None,
    max_commands: int = 5000,
    max_helper_calls: int = 200,
    db_suffix: str = "",
) -> EpisodeResult:
    """Run a single policy against a freshly-seeded world.

    The policy file must define `run(env)`. The env exposes
    `run_command` and `classify` (see policy_exp.api.PolicyEnv)."""
    cfg = load_config(config_name)
    horizon = horizon_years if horizon_years is not None else cfg.sim.horizon_years

    db_url = _build_db_url(config_name, model, seed, suffix=db_suffix)
    _wipe_db_file(db_url)
    os.environ["DATABASE_URL"] = db_url
    os.environ["YC_BENCH_EXPERIMENT"] = config_name

    engine = build_engine()
    init_db(engine)
    factory = build_session_factory(engine)

    @contextmanager
    def db_factory(_factory=factory):
        with session_scope(_factory) as session:
            yield session

    seed_args = SimpleNamespace(
        seed=seed,
        company_name=company_name,
        start_date=start_date,
    )
    company_id = _init_simulation(db_factory, seed_args, cfg, horizon)
    initial = _read_company_state(factory, company_id)

    env_kwargs = dict(max_commands=max_commands, max_helper_calls=max_helper_calls)
    if helper_model:
        env_kwargs["helper_model"] = helper_model
    env = PolicyEnv(**env_kwargs)

    run_fn = load_policy(policy_path)

    error_msg: str | None = None
    logger.info(
        "Policy run: model=%s seed=%d cfg=%s db=%s timeout=%ss",
        model, seed, config_name, db_url, wall_timeout_seconds,
    )
    exc, elapsed = _exec_with_timeout(run_fn, (env,), wall_timeout_seconds)
    if exc is not None:
        if isinstance(exc, PolicyTerminated):
            logger.info("Policy terminated cleanly: %s", exc)
        elif isinstance(exc, PolicyBudgetExceeded):
            error_msg = f"budget: {exc}"
            logger.warning("Policy hit budget: %s", exc)
        else:
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.warning("Policy raised: %s\n%s", exc, "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ))

    final = _read_company_state(factory, company_id)
    time_series = extract_time_series(db_factory, company_id)

    result = EpisodeResult(
        model=model,
        seed=seed,
        config_name=config_name,
        policy_path=str(policy_path),
        final_funds_cents=final["funds_cents"],
        initial_funds_cents=initial["funds_cents"],
        funds_delta_cents=final["funds_cents"] - initial["funds_cents"],
        bankrupt=final["bankrupt"],
        terminal_reason=env.terminal_reason
        or ("bankruptcy" if final["bankrupt"] else None),
        sim_commands=env.turn,
        helper_calls=env.helper_calls,
        wall_seconds=round(elapsed, 2),
        error=error_msg,
        time_series=time_series,
        helper_log=env.helper_log,
        command_log_tail=env.command_log[-50:],
    )

    engine.dispose()
    return result


def write_result(result: EpisodeResult, results_dir: Path | str = "results") -> Path:
    results_dir = Path(results_dir)
    results_dir.mkdir(exist_ok=True)
    slug = result.model.replace("/", "_")
    path = results_dir / f"policy_{result.config_name}_{result.seed}_{slug}.json"
    path.write_text(json.dumps(result.to_dict(), indent=2, default=str))
    return path


__all__ = ["EpisodeResult", "run_policy_episode", "write_result"]
