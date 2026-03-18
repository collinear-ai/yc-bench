"""System prompt and user-message builders for the YC-Bench agent."""
from __future__ import annotations

SYSTEM_PROMPT = """\
You are the CEO of a startup in a business simulation. Maximize funds and prestige while avoiding bankruptcy.

All actions use `yc-bench` CLI commands via `run_command`. All return JSON.

## Commands

### Observe
- `yc-bench company status` — funds, prestige per domain, employee count, payroll
- `yc-bench employee list` — employees with IDs, tiers, salaries, skill rates per domain
- `yc-bench market browse [--domain X] [--reward-min-cents N] [--limit N] [--offset N]` — available tasks (auto-filtered to your prestige). Shows client_name, required_trust, reward, requirements.
- `yc-bench task list [--status X]` — your tasks by status
- `yc-bench task inspect --task-id <UUID>` — task details: requirements, assignments, progress
- `yc-bench client list` — all clients with trust levels and specialties
- `yc-bench client history` — per-client success/failure rates
- `yc-bench finance ledger [--from MM/DD/YYYY] [--to MM/DD/YYYY] [--category X]` — financial history
- `yc-bench scratchpad read` — your persistent notes

### Act
- `yc-bench task accept --task-id <UUID>` — accept a task from market
- `yc-bench task assign --task-id <UUID> --employee-id <UUID>` — assign specific employee (optional — dispatch auto-assigns all if none assigned)
- `yc-bench task dispatch --task-id <UUID>` — start work (auto-assigns all employees if none pre-assigned)
- `yc-bench task cancel --task-id <UUID> --reason "text"` — cancel a task (prestige penalty)
- `yc-bench sim resume` — advance time to next event
- `yc-bench scratchpad write --content "text"` — save notes (context gets truncated, scratchpad persists)
- `yc-bench scratchpad append --content "text"` — append to notes
- `yc-bench scratchpad clear` — erase notes

## Rules

- Success (before deadline) = reward + prestige + trust gain. Failure = prestige penalty, no reward.
- Employee throughput splits across active tasks with diminishing penalty. Two concurrent tasks each run at ~71% speed (not 50%), so mild parallelism is faster than sequential.
- Payroll deducted monthly. Funds below zero = bankruptcy.
- `sim resume` advances to next event. Do NOT call it without active tasks — it skips to payroll with zero revenue.
- Check command results. If `task accept` fails, try a different task before calling `sim resume`.

## How the Environment Works

- Higher-prestige tasks pay more. Market browse auto-filters to your current prestige level.
- Prestige grows independently per domain — visible in `company status`.
- Each employee has different skill rates per domain. Output depends on their rate in the task's domain.
- Business hours are weekdays 09:00-18:00. Payroll deducted on the 1st of each month.

## Clients & Trust

- Each task belongs to a client. Each client has specialty domains — tasks are biased toward their specialties.
- Completing tasks builds trust [0-5] with that client. Trust gains diminish as you approach max.
- Higher trust = less work per task. Some tasks require minimum trust to accept.
- Working for one client erodes trust with others.
- Not all clients are equally reliable. Use `client history` to check success/failure rates.
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
                title = ev.get("task_title") or ev.get("task_id", "?")
                client = ev.get("client_name", "")
                client_str = f" (client: {client})" if client else ""
                funds = ev.get("funds_delta", 0)
                funds_str = f" +${funds/100:,.0f}" if success and funds else ""
                parts.append(f"- {title}{client_str}: {'SUCCESS' + funds_str if success else 'FAILED — missed deadline, no reward'}")
            elif ev_type == "task_half":
                pct = ev.get("milestone_pct", "?")
                parts.append(f"- Task {ev.get('task_id', '?')}: {pct}% progress reached")
            elif ev_type == "payment_dispute":
                clawback = ev.get("clawback_cents", 0)
                client_name = ev.get("client_name", "unknown")
                parts.append(f"- PAYMENT DISPUTE from {client_name}: -${clawback / 100:,.2f} clawed back")
            elif ev_type == "horizon_end":
                parts.append("- **Horizon end reached. Simulation complete.**")
            elif ev_type == "bankruptcy":
                parts.append("- **BANKRUPTCY. Simulation terminated.**")
            else:
                parts.append(f"- Event: {ev_type}")

    if active_tasks == 0 and planned_tasks == 0:
        parts.append(
            "\n**ACTION REQUIRED**: No tasks are running. "
            "Do NOT call `sim resume` — it will just burn payroll with zero revenue. "
            "Accept a task, assign employees to it, and dispatch it first."
        )
    elif planned_tasks > 0 and active_tasks == 0:
        parts.append(
            "\n**ACTION REQUIRED**: You have planned tasks but none are dispatched. "
            "Do NOT call `sim resume` yet — dispatch first or you'll just burn payroll. "
            "Assign employees and dispatch now."
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
    episode: int = 1,
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

    lines = []
    if episode > 1:
        lines.extend([
            f"## Episode {episode} — Restarting After Bankruptcy",
            "",
            f"You went bankrupt in episode {episode - 1}. The simulation has been reset,",
            "but your **scratchpad notes from the previous episode are preserved**.",
            "Read your scratchpad (`yc-bench scratchpad read`) to review your notes",
            "and learn from past mistakes before taking action.",
            "",
        ])
    lines.extend([
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
        "Complete these steps now (multiple commands per turn are fine):",
        "1. `yc-bench market browse` — see available tasks",
        "2. `yc-bench task accept --task-id <UUID>` — accept a task",
        "3. `yc-bench task dispatch --task-id <UUID>` — start work (auto-assigns all if none pre-assigned)",
        "4. `yc-bench sim resume` — advance time",
        "",
        "**IMPORTANT**: Check each command's result before proceeding to the next.",
        "If `task accept` fails (trust or prestige too low), try a different task.",
        "Do NOT call `sim resume` unless you have at least one active task — it will skip forward with zero revenue.",
    ])
    if bankrupt:
        lines.append("WARNING: company is already bankrupt at initialization.")
    return "\n".join(lines)


__all__ = ["SYSTEM_PROMPT", "build_turn_context", "build_initial_user_prompt"]
