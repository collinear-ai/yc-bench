from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from decimal import Decimal
from typing import Optional
from uuid import UUID

import typer

from ..db.session import build_engine, build_session_factory, init_db, session_scope

app = typer.Typer(name="yc-bench", add_completion=False)


# ---------------------------------------------------------------------------
# Helpers shared across command modules
# ---------------------------------------------------------------------------

@contextmanager
def get_db():
    """Yield a transactional SQLAlchemy session, commit on success."""
    engine = build_engine()
    init_db(engine)
    factory = build_session_factory(engine)
    with session_scope(factory) as session:
        yield session


class _JSONEncoder(json.JSONEncoder):
    """Handle UUID, Decimal, datetime serialisation."""

    def default(self, o):
        if isinstance(o, UUID):
            return str(o)
        if isinstance(o, Decimal):
            return float(o)
        from datetime import datetime, date
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, date):
            return o.isoformat()
        return super().default(o)


def json_output(data: dict | list) -> None:
    """Print JSON to stdout (captured by run_command executor)."""
    typer.echo(json.dumps(data, cls=_JSONEncoder, indent=2))


def error_output(message: str, code: int = 1) -> None:
    """Print JSON error and exit."""
    typer.echo(json.dumps({"error": message}), err=False)
    raise typer.Exit(code=code)


# ---------------------------------------------------------------------------
# Register sub-command groups
# ---------------------------------------------------------------------------

from .sim_commands import sim_app        # noqa: E402
from .company_commands import company_app  # noqa: E402
from .market_commands import market_app    # noqa: E402
from .task_commands import task_app        # noqa: E402
from .finance_commands import finance_app  # noqa: E402
from .report_commands import report_app    # noqa: E402
from .employee_commands import employee_app  # noqa: E402
from .scratchpad_commands import scratchpad_app  # noqa: E402

app.add_typer(sim_app, name="sim")
app.add_typer(company_app, name="company")
app.add_typer(employee_app, name="employee")
app.add_typer(market_app, name="market")
app.add_typer(task_app, name="task")
app.add_typer(finance_app, name="finance")
app.add_typer(report_app, name="report")
app.add_typer(scratchpad_app, name="scratchpad")


@app.command("start")
def start_command_cli():
    """Interactive 3-step quickstart: pick model, enter key, choose difficulty, run."""
    from .start_command import start_interactive
    start_interactive()


@app.command("run")
def run_command_cli(
    model: str = typer.Option(..., help="LiteLLM model string (e.g. openrouter/z-ai/glm-5)"),
    seed: int = typer.Option(..., help="Random seed for deterministic world generation"),
    horizon_years: Optional[int] = typer.Option(None, help="Simulation horizon in years (default from config)"),
    company_name: str = typer.Option("BenchCo", help="Name of the simulated company"),
    start_date: str = typer.Option("2025-01-01", help="Simulation start date (YYYY-MM-DD)"),
    config_name: str = typer.Option(
        "default", "--config",
        help="Preset name ('default', 'fast_test', 'high_reward') or path to a .toml file",
    ),
    no_live: bool = typer.Option(False, "--no-live", help="Disable the live terminal dashboard"),
):
    """Run a full benchmark: migrate DB, seed world, run agent loop to completion."""
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(usecwd=True), override=False)

    from ..runner.main import run_benchmark
    from ..runner.args import RunArgs
    args = RunArgs(
        model=model,
        seed=seed,
        horizon_years=horizon_years,
        company_name=company_name,
        start_date=start_date,
        config_name=config_name,
        no_live=no_live,
    )
    raise SystemExit(run_benchmark(args))


def app_main():
    """Entry point for `yc-bench` console_script."""
    app()
