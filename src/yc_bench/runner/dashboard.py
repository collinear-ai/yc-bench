"""Live terminal dashboard for YC-Bench using Rich."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


SPARK_CHARS = "▁▂▃▄▅▆▇█"

# Domain → (display name, color) for styled inline display
DOMAIN_STYLE = {
    "research":         ("Research",  "bright_magenta"),
    "inference":        ("Inference", "bright_cyan"),
    "data_environment": ("Data/Env",  "bright_blue"),
    "training":         ("Training",  "red"),
}


def _sparkline(values: list[float], width: int = 20) -> str:
    """Return a Unicode sparkline string from a list of values."""
    if not values:
        return ""
    vals = values[-width:]
    lo, hi = min(vals), max(vals)
    span = hi - lo if hi != lo else 1.0
    return "".join(SPARK_CHARS[min(int((v - lo) / span * (len(SPARK_CHARS) - 1)), len(SPARK_CHARS) - 1)] for v in vals)


def _fmt_dollars(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _fmt_delta(cents: int) -> str:
    sign = "+" if cents >= 0 else "-"
    return f"{sign}${abs(cents) / 100:,.0f}"


def _domain_tag(domain_str: str) -> str:
    """Colored domain tag like [bright_cyan]SYS[/bright_cyan]."""
    label, color = DOMAIN_STYLE.get(domain_str, (domain_str[:3].upper(), "white"))
    return f"[{color}]{label}[/{color}]"


def _mini_bar(pct: float, width: int = 8) -> str:
    """Colored progress bar: green when done, yellow partial, dim empty."""
    filled = int(pct * width)
    if pct >= 1.0:
        return f"[bold green]{'=' * width}[/bold green]"
    elif pct >= 0.5:
        return f"[yellow]{'=' * filled}[/yellow][dim]{'.' * (width - filled)}[/dim]"
    else:
        return f"[red]{'=' * filled}[/red][dim]{'.' * (width - filled)}[/dim]"


@dataclass
class TaskInfo:
    title: str
    status: str
    prestige: int
    reward_dollars: float
    deadline: str
    domains: list[str]
    progress: list[tuple[str, float, float]]  # [(domain, completed, required)]


@dataclass
class EmployeeInfo:
    name: str
    salary_dollars: float
    skills: list[tuple[str, float]]  # [(domain, rate)]


@dataclass
class DashboardState:
    model: str = ""
    seed: int = 0
    config_name: str = ""
    turn: int = 0
    sim_date: str = ""
    horizon_end: str = ""
    funds_cents: int = 0
    funds_delta_cents: int = 0
    funds_history: list[float] = field(default_factory=list)
    runway_months: float = 0.0
    active_tasks: int = 0
    planned_tasks: int = 0
    employee_count: int = 0
    monthly_payroll_cents: int = 0
    api_cost_usd: float = 0.0
    turn_time_sec: float = 0.0
    last_action: str = ""
    status: str = ""
    elapsed_sec: float = 0.0
    tasks_detail: list[TaskInfo] = field(default_factory=list)
    employees_detail: list[EmployeeInfo] = field(default_factory=list)
    completed_count: int = 0
    failed_count: int = 0


def _query_detailed_snapshot(db_factory, company_id) -> dict[str, Any]:
    """Query rich task/employee details from the DB for dashboard display."""
    from ..db.models.task import Task, TaskStatus, TaskRequirement
    from ..db.models.employee import Employee, EmployeeSkillRate

    with db_factory() as db:
        tasks_detail = []
        for status in (TaskStatus.ACTIVE, TaskStatus.PLANNED):
            tasks = db.query(Task).filter(
                Task.company_id == company_id,
                Task.status == status,
            ).all()
            for t in tasks:
                reqs = db.query(TaskRequirement).filter(
                    TaskRequirement.task_id == t.id,
                ).all()
                domains = [r.domain.value for r in reqs]
                progress = [
                    (r.domain.value, float(r.completed_qty), float(r.required_qty))
                    for r in reqs
                ]
                deadline_str = t.deadline.strftime("%Y-%m-%d") if t.deadline else "-"
                tasks_detail.append(TaskInfo(
                    title=t.title,
                    status=status.value,
                    prestige=t.required_prestige,
                    reward_dollars=t.reward_funds_cents / 100.0,
                    deadline=deadline_str,
                    domains=domains,
                    progress=progress,
                ))

        from sqlalchemy import func
        completed_count = db.query(func.count(Task.id)).filter(
            Task.company_id == company_id,
            Task.status == TaskStatus.COMPLETED_SUCCESS,
        ).scalar() or 0
        failed_count = db.query(func.count(Task.id)).filter(
            Task.company_id == company_id,
            Task.status == TaskStatus.COMPLETED_FAIL,
        ).scalar() or 0

        employees_detail = []
        employees = db.query(Employee).filter(
            Employee.company_id == company_id,
        ).all()
        for emp in employees:
            skills = db.query(EmployeeSkillRate).filter(
                EmployeeSkillRate.employee_id == emp.id,
            ).all()
            skill_list = [
                (s.domain.value, float(s.rate_domain_per_hour))
                for s in sorted(skills, key=lambda s: float(s.rate_domain_per_hour), reverse=True)
            ]
            employees_detail.append(EmployeeInfo(
                name=emp.name,
                salary_dollars=emp.salary_cents / 100.0,
                skills=skill_list,
            ))

    return {
        "tasks_detail": tasks_detail,
        "employees_detail": employees_detail,
        "completed_count": completed_count,
        "failed_count": failed_count,
    }


class BenchmarkDashboard:
    """Rich Live dashboard for benchmark progress."""

    def __init__(self, model: str, seed: int, config_name: str,
                 db_factory=None, company_id=None):
        self._console = Console()
        self._live: Live | None = None
        self._state = DashboardState(model=model, seed=seed, config_name=config_name)
        self._start_time = time.monotonic()
        self._turn_start_time = 0.0
        self._prev_funds_cents = 0
        self._db_factory = db_factory
        self._company_id = company_id
        self._stderr_backup = None
        self._devnull = None

    def start(self) -> None:
        import sys
        self._start_time = time.monotonic()
        self._state.status = "[dim]Starting...[/dim]"
        self._stderr_backup = sys.stderr
        self._devnull = open(os.devnull, "w")
        sys.stderr = self._devnull
        self._live = Live(
            self._render(),
            console=self._console,
            refresh_per_second=2,
            screen=True,
        )
        self._live.start()

    def stop(self) -> None:
        import sys
        if self._live is not None:
            self._live.stop()
            self._live = None
        if self._stderr_backup is not None:
            sys.stderr = self._stderr_backup
            self._stderr_backup = None
        if self._devnull is not None:
            self._devnull.close()
            self._devnull = None

    def mark_turn_start(self, turn_num: int) -> None:
        self._turn_start_time = time.monotonic()
        self._state.turn = turn_num
        self._state.status = f"[yellow]>> Turn {turn_num}: waiting for LLM...[/yellow]"
        self._state.elapsed_sec = time.monotonic() - self._start_time
        self._refresh()

    def update(self, snapshot: dict[str, Any], run_state: Any, commands: list[str] | None = None) -> None:
        now = time.monotonic()
        s = self._state

        s.turn = run_state.turn_count
        s.sim_date = snapshot.get("sim_time", "")[:10]
        s.horizon_end = snapshot.get("horizon_end", "")[:10]
        s.funds_cents = snapshot.get("funds_cents", 0)
        s.funds_delta_cents = s.funds_cents - self._prev_funds_cents
        self._prev_funds_cents = s.funds_cents
        s.funds_history.append(s.funds_cents / 100.0)
        s.active_tasks = snapshot.get("active_tasks", 0)
        s.planned_tasks = snapshot.get("planned_tasks", 0)
        s.employee_count = snapshot.get("employee_count", 0)
        s.monthly_payroll_cents = snapshot.get("monthly_payroll_cents", 0)
        s.api_cost_usd = run_state.total_cost_usd
        s.turn_time_sec = now - self._turn_start_time if self._turn_start_time else 0.0
        s.elapsed_sec = now - self._start_time

        if s.monthly_payroll_cents > 0:
            s.runway_months = s.funds_cents / s.monthly_payroll_cents
        else:
            s.runway_months = float("inf")

        if commands:
            first = commands[0].split(" -> ")[0] if " -> " in commands[0] else commands[0]
            if len(commands) > 1:
                s.last_action = f"{first} (+{len(commands)-1} more)"
            else:
                s.last_action = first
        else:
            s.last_action = "(no commands)"

        if run_state.terminal:
            reason = run_state.terminal_reason.value if run_state.terminal_reason else "unknown"
            s.status = f"[bold green]DONE: {reason}[/bold green]"
        else:
            s.status = f"[green]Turn {s.turn} complete[/green]"

        if self._db_factory is not None and self._company_id is not None:
            try:
                detail = _query_detailed_snapshot(self._db_factory, self._company_id)
                s.tasks_detail = detail["tasks_detail"]
                s.employees_detail = detail["employees_detail"]
                s.completed_count = detail["completed_count"]
                s.failed_count = detail["failed_count"]
            except Exception:
                pass

        self._refresh()

    def print_final_summary(self, run_state: Any) -> None:
        s = self._state
        elapsed_m, elapsed_s = divmod(int(s.elapsed_sec), 60)
        elapsed_h, elapsed_m = divmod(elapsed_m, 60)

        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column(style="bold cyan", width=14)
        table.add_column()

        table.add_row("Turns", str(s.turn))
        table.add_row("Final Funds", _fmt_dollars(s.funds_cents))
        table.add_row("Tasks", f"[green]{s.completed_count} done[/green] / [red]{s.failed_count} failed[/red]")
        table.add_row("API Cost", f"${s.api_cost_usd:.4f}")
        table.add_row("Elapsed", f"{elapsed_h}h {elapsed_m:02d}m {elapsed_s:02d}s")
        reason = run_state.terminal_reason.value if run_state.terminal_reason else "max_turns"
        table.add_row("Outcome", reason)

        panel = Panel(
            table,
            title="[bold]YC-Bench Complete[/bold]",
            border_style="green" if reason == "horizon_end" else "red" if reason == "bankruptcy" else "yellow",
        )
        self._console.print(panel)

    def _refresh(self) -> None:
        if self._live is not None:
            self._live.update(self._render())

    # ------------------------------------------------------------------
    # Render helpers
    # ------------------------------------------------------------------

    def _render_stats_panel(self) -> Panel:
        s = self._state
        elapsed_m, elapsed_s = divmod(int(s.elapsed_sec), 60)
        elapsed_h, elapsed_m = divmod(elapsed_m, 60)
        short_model = s.model.rsplit("/", 1)[-1]

        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column(style="bold cyan", width=12)
        table.add_column(overflow="ellipsis", no_wrap=True)

        table.add_row("Model", f"[bold]{short_model}[/bold]  seed={s.seed}  {s.config_name}")
        table.add_row("Turn", f"[bold white]{s.turn}[/bold white]")
        table.add_row("Sim Date", f"{s.sim_date} [dim]->[/dim] {s.horizon_end}" if s.sim_date else "[dim]--[/dim]")
        table.add_row("Elapsed", f"{elapsed_h}h {elapsed_m:02d}m {elapsed_s:02d}s")

        # Funds with colored sparkline
        spark = _sparkline(s.funds_history)
        delta_color = "green" if s.funds_delta_cents >= 0 else "red"
        if s.turn > 0:
            funds_str = f"[bold]{_fmt_dollars(s.funds_cents)}[/bold] [{delta_color}]{_fmt_delta(s.funds_delta_cents)}[/{delta_color}] [{delta_color}]{spark}[/{delta_color}]"
        else:
            funds_str = "[dim]--[/dim]"
        table.add_row("Funds", funds_str)

        # Runway with urgency coloring
        if s.runway_months == float("inf"):
            runway_str = "[green]unlimited[/green]"
        elif s.runway_months < 2:
            runway_str = f"[bold red blink]{s.runway_months:.1f}mo CRITICAL[/bold red blink]"
        elif s.runway_months < 4:
            runway_str = f"[bold yellow]{s.runway_months:.1f}mo LOW[/bold yellow]"
        else:
            runway_str = f"[green]{s.runway_months:.1f}mo[/green]"
        table.add_row("Runway", runway_str)

        # Task scoreboard
        task_parts = f"{s.active_tasks} active / {s.planned_tasks} queued"
        if s.completed_count or s.failed_count:
            task_parts += f"  [green]{s.completed_count} done[/green] [red]{s.failed_count} fail[/red]"
        table.add_row("Tasks", task_parts)

        table.add_row("Team", f"{s.employee_count} people  {_fmt_dollars(s.monthly_payroll_cents)}/mo" if s.monthly_payroll_cents else str(s.employee_count))
        table.add_row("Cost", f"${s.api_cost_usd:.4f}  ({s.turn_time_sec:.1f}s/turn)" if s.turn_time_sec else f"${s.api_cost_usd:.4f}")
        table.add_row("Action", s.last_action or "[dim]--[/dim]")
        table.add_row("Status", s.status)

        return Panel(table, title="[bold]YC-Bench[/bold]", border_style="blue")

    def _render_tasks_panel(self) -> Panel:
        s = self._state

        if not s.tasks_detail:
            return Panel(
                "[dim]No active or planned tasks yet...[/dim]",
                title="[bold]Tasks[/bold]",
                border_style="yellow",
            )

        table = Table(box=None, padding=(0, 1), show_edge=False)
        table.add_column("", width=2)                                           # status marker
        table.add_column("Task", style="bold white", no_wrap=True, max_width=20)
        table.add_column("$$$", width=8, justify="right", no_wrap=True)         # reward
        table.add_column("Due", width=10, no_wrap=True)                         # deadline
        table.add_column("Progress", no_wrap=True, overflow="ellipsis", ratio=1)

        for t in s.tasks_detail[:6]:
            if t.status == "active":
                marker = "[bold green]>>[/bold green]"
            else:
                marker = "[dim]..[/dim]"

            # Prestige stars in yellow
            stars = f"[yellow]{'*' * min(t.prestige, 5)}[/yellow]"

            # Reward colored by size
            if t.reward_dollars >= 50000:
                reward = f"[bold green]${t.reward_dollars:,.0f}[/bold green]"
            elif t.reward_dollars >= 20000:
                reward = f"[green]${t.reward_dollars:,.0f}[/green]"
            else:
                reward = f"${t.reward_dollars:,.0f}"

            # Domain progress with colored bars
            prog_parts = []
            for domain, completed, required in t.progress:
                pct = completed / required if required > 0 else 0
                bar = _mini_bar(pct, width=6)
                tag = _domain_tag(domain)
                prog_parts.append(f"{tag} {bar}")
            progress_str = " ".join(prog_parts)

            table.add_row(marker, t.title, reward, t.deadline, progress_str)

        remaining = len(s.tasks_detail) - 6
        if remaining > 0:
            table.add_row("", f"[dim]+{remaining} more[/dim]", "", "", "")

        return Panel(table, title="[bold]Tasks[/bold]", border_style="yellow")

    def _render_team_panel(self) -> Panel:
        s = self._state

        if not s.employees_detail:
            return Panel("[dim]No employees hired yet...[/dim]", title="[bold]Team[/bold]", border_style="magenta")

        table = Table(box=None, padding=(0, 1), show_edge=False)
        table.add_column("Name", style="bold white", width=14, no_wrap=True)
        table.add_column("Pay", width=8, justify="right", no_wrap=True)
        table.add_column("Skills", no_wrap=True, overflow="ellipsis", ratio=1)

        for emp in s.employees_detail:
            # Salary colored by cost
            if emp.salary_dollars >= 10000:
                pay = f"[bold red]${emp.salary_dollars:,.0f}[/bold red]"
            elif emp.salary_dollars >= 5000:
                pay = f"[yellow]${emp.salary_dollars:,.0f}[/yellow]"
            else:
                pay = f"[green]${emp.salary_dollars:,.0f}[/green]"

            # Skill bars — top 3
            skill_parts = []
            for d, r in emp.skills[:3]:
                tag = _domain_tag(d)
                # Rate bar: scale 0-15 to a mini bar
                bar_pct = min(r / 15.0, 1.0)
                bar = _mini_bar(bar_pct, width=4)
                skill_parts.append(f"{tag}{bar}")
            skills_str = " ".join(skill_parts)

            table.add_row(emp.name[:14], pay, skills_str)

        return Panel(table, title="[bold]Team[/bold]", border_style="magenta")

    def _render(self) -> Group:
        return Group(
            self._render_stats_panel(),
            self._render_tasks_panel(),
            self._render_team_panel(),
        )


__all__ = ["BenchmarkDashboard", "DashboardState"]
