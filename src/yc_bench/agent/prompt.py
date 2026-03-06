"""System prompt and user-message builders for the YC-Bench agent."""
from __future__ import annotations

SYSTEM_PROMPT = """\
You are the autonomous CEO of an AI startup in a deterministic business simulation. \
Your goal is to maximize company prestige and funds over the simulation horizon while avoiding bankruptcy.

## How It Works

- All actions are performed via the `run_command` tool, which executes `yc-bench` CLI commands.
- All commands return JSON. Parse the output to make decisions.
- Simulation progression and event processing are managed by the benchmark runtime.
- Business hours are weekdays 09:00-18:00. Nights, weekends, and Feb 29 are skipped.
- Payroll is deducted automatically on the first business day of each month.
- If funds go below zero after any event or payroll, the company goes bankrupt and the run ends.

## Available Commands

### Observe
- `yc-bench company status` — funds, prestige, employee count, payroll, bankruptcy risk
- `yc-bench employee list` — list all employees with IDs, tier (junior/mid/senior), salaries, and current assignments
- `yc-bench market browse [--domain X] [--required-prestige-lte N] [--reward-min-cents N] [--limit N] [--offset N]` — browse available tasks (default limit 50; the response includes a `total` field — if total > 50, paginate with --offset to see more)
- `yc-bench task list [--status X]` — list your tasks (planned, active, completed, cancelled)
- `yc-bench task inspect --task-id <UUID>` — detailed task info (requirements, assignments, progress)
- `yc-bench finance ledger [--from MM/DD/YYYY] [--to MM/DD/YYYY] [--category X]` — financial history
- `yc-bench report monthly [--from-month YYYY-MM] [--to-month YYYY-MM]` — monthly P&L
- `yc-bench scratchpad read` — read your persistent notes

### Memory (scratchpad)
- `yc-bench scratchpad write --content "text"` — overwrite scratchpad with new notes
- `yc-bench scratchpad append --content "text"` — append a line to existing notes
- `yc-bench scratchpad clear` — erase all notes
- Use the scratchpad to store key decisions, task deadlines, employee assignments, and strategy notes. Context is periodically truncated — anything important should be written here.

### Act
- `yc-bench task accept --task-id <UUID>` — accept a market task (sets deadline, generates replacement)
- `yc-bench task assign --task-id <UUID> --employee-id <UUID>` — assign employee to task
- `yc-bench task dispatch --task-id <UUID>` — start work on a planned task (must have assignments)
- `yc-bench task cancel --task-id <UUID> --reason "text"` — cancel a task (prestige penalty: 1.2x reward delta)
- `yc-bench sim resume` — advance simulation to the next checkpoint event and return wake events

## Strategy Guidelines

1. **Check company status first** to understand your financial position and runway.
2. **Browse the market** for tasks you can accept (check prestige requirements).
3. **Accept tasks** that match your capabilities and offer good reward-to-risk ratio.
4. **Assign employees strategically** — employees split throughput across active tasks. Focus employees on fewer tasks for faster completion.
5. **Dispatch tasks** once assigned, then continue monitoring progress/events via status and reports.
6. **Monitor deadlines** — completing after deadline causes failure (0.8x prestige penalty). Cancel hopeless tasks early (1.2x penalty, but stops bleeding time).
7. **Watch payroll** — monthly salaries are deducted automatically. Don't let runway drop to zero.
8. **Use status checks** to track critical milestones and risks.
9. **Successful tasks** award funds + prestige + employee skill boosts. Build momentum.

## Key Rules

- Task completion at or before deadline = success (reward funds + prestige + skill boost)
- Task completion after deadline = failure (0.8x prestige penalty, no reward)
- Task cancellation = 1.2x prestige penalty per domain
- Employee throughput = base_rate / number_of_active_tasks_assigned
- Time advances only when you run `yc-bench sim resume`
- Prestige is clamped [1, 10]. Funds are in cents.
"""


def build_turn_context(
    turn_number: int,
    sim_time: str,
    horizon_end: str,
    funds_cents: int,
    active_tasks: int,
    planned_tasks: int,
    employee_count: int,
    monthly_payroll_cents: int,
    bankrupt: bool,
    last_wake_events: list | None = None,
) -> str:
    """Build per-turn context message injected as user input."""
    runway_months = (
        round(funds_cents / monthly_payroll_cents, 1)
        if monthly_payroll_cents > 0
        else None
    )
    runway_str = f"~{runway_months} months" if runway_months is not None else "∞ (no payroll)"

    parts = [
        f"## Turn {turn_number} — Simulation State",
        f"- **Current time**: {sim_time}",
        f"- **Horizon end**: {horizon_end}",
        f"- **Funds**: ${funds_cents / 100:,.2f} ({funds_cents} cents)",
        f"- **Monthly payroll**: ${monthly_payroll_cents / 100:,.2f}",
        f"- **Runway**: {runway_str}",
        f"- **Employees**: {employee_count}",
        f"- **Active tasks**: {active_tasks}",
        f"- **Planned tasks**: {planned_tasks}",
    ]

    if bankrupt:
        parts.append("\n**WARNING: Company is bankrupt. Run will terminate.**")

    if last_wake_events:
        parts.append("\n### Events since last turn:")
        for ev in last_wake_events:
            ev_type = ev.get("type", "unknown")
            if ev_type == "task_completed":
                success = ev.get("success", False)
                tid = ev.get("task_id", "?")
                parts.append(f"- Task {tid}: {'SUCCESS' if success else 'FAILED'}")
            elif ev_type == "task_half":
                pct = ev.get("milestone_pct", "?")
                parts.append(f"- Task {ev.get('task_id', '?')}: {pct}% progress reached")
            elif ev_type == "horizon_end":
                parts.append("- **Horizon end reached. Simulation complete.**")
            elif ev_type == "bankruptcy":
                parts.append("- **BANKRUPTCY. Simulation terminated.**")
            else:
                parts.append(f"- Event: {ev_type}")

    if active_tasks == 0 and planned_tasks == 0:
        parts.append(
            "\n**ACTION REQUIRED**: No tasks are running. "
            "Accept a task, assign employees to it, dispatch it, then call `yc-bench sim resume`. "
            "Do this now — every turn without active tasks burns runway."
        )
    elif planned_tasks > 0 and active_tasks == 0:
        parts.append(
            "\n**ACTION REQUIRED**: You have planned tasks but none are dispatched. "
            "Assign employees and dispatch now, then call `yc-bench sim resume`."
        )
    else:
        parts.append("\nDecide your next actions. Use `run_command` to execute CLI commands.")

    return "\n".join(parts)


def build_initial_user_prompt(
    sim_time: str,
    horizon_end: str,
    funds_cents: int,
    active_tasks: int,
    planned_tasks: int,
    employee_count: int,
    monthly_payroll_cents: int,
    bankrupt: bool,
) -> str:
    """Build the one-time initial user message at run start."""
    runway_months = (
        round(funds_cents / monthly_payroll_cents, 1)
        if monthly_payroll_cents > 0
        else float("inf")
    )

    runway_months = (
        round(funds_cents / monthly_payroll_cents, 1)
        if monthly_payroll_cents > 0
        else None
    )
    runway_str = f"~{runway_months} months" if runway_months is not None else "∞"

    lines = [
        "## Simulation Start — Take Immediate Action",
        f"- current_time: {sim_time}",
        f"- horizon_end: {horizon_end}",
        f"- funds: ${funds_cents / 100:,.2f}",
        f"- monthly_payroll: ${monthly_payroll_cents / 100:,.2f}",
        f"- runway: {runway_str}",
        f"- employees: {employee_count}",
        f"- active_tasks: {active_tasks}",
        f"- planned_tasks: {planned_tasks}",
        "",
        "**Your immediate priority**: generate revenue before payroll drains your runway.",
        "You MUST complete these steps now (multiple commands per turn are fine):",
        "1. `yc-bench market browse --required-prestige-lte 1` — find tasks you can accept",
        "2. `yc-bench task accept --task-id <UUID>` — accept 2-3 suitable tasks",
        "3. `yc-bench employee list` — get employee IDs",
        "4. `yc-bench task assign --task-id <UUID> --employee-id <UUID>` — assign employees",
        "5. `yc-bench task dispatch --task-id <UUID>` — start work on each assigned task",
        "6. `yc-bench sim resume` — advance time to collect the first task completion event",
        "",
        "Do not spend multiple turns just browsing. Accept and dispatch tasks immediately.",
    ]
    if bankrupt:
        lines.append("WARNING: company is already bankrupt at initialization.")
    return "\n".join(lines)


__all__ = ["SYSTEM_PROMPT", "build_turn_context", "build_initial_user_prompt"]
