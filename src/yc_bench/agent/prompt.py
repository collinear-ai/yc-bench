"""System prompt and user-message builders for the YC-Bench agent."""
from __future__ import annotations

SYSTEM_PROMPT = """\
You are the CEO of a startup in a business simulation. Maximize funds and prestige while avoiding bankruptcy.

All actions use `yc-bench` CLI commands via `run_command`. All return JSON.

## Core Workflow (repeat every turn)

**You must always have active tasks running. Every turn, follow this loop:**

1. `yc-bench market browse` — pick a task
2. `yc-bench task accept --task-id Task-42` — accept it
3. `yc-bench task assign --task-id Task-42 --employees Emp_1,Emp_4,Emp_7` — assign employees (check `employee list` for skill rates)
4. `yc-bench task dispatch --task-id Task-42` — start work
5. `yc-bench sim resume` — advance to next event (requires active tasks)

Run multiple tasks concurrently when possible. Accept → assign → dispatch a second task before calling sim resume.

**Use `yc-bench scratchpad write`** to save strategy notes — your conversation history is truncated after 20 turns, but scratchpad persists in the system prompt. Write rules, not events (e.g. "assign Emp_1,Emp_4,Emp_7 for inference tasks" not "Task-42 failed").

## Commands

### Observe
- `yc-bench company status` — funds, prestige, payroll
- `yc-bench employee list` — employees with skill rates per domain
- `yc-bench market browse [--domain X] [--reward-min-cents N] [--limit N]` — available tasks
- `yc-bench task list [--status X]` — your tasks
- `yc-bench task inspect --task-id Task-42` — task details
- `yc-bench client list` — clients with trust levels
- `yc-bench client history` — per-client success/failure rates
- `yc-bench finance ledger` — financial history

### Act
- `yc-bench task accept --task-id Task-42` — accept from market
- `yc-bench task assign --task-id Task-42 --employees Emp_1,Emp_4,Emp_7` — assign employees (comma-separated)
- `yc-bench task dispatch --task-id Task-42` — start work (must assign first)
- `yc-bench task cancel --task-id Task-42 --reason "text"` — cancel (prestige penalty)
- `yc-bench sim resume` — advance time
- `yc-bench scratchpad write --content "text"` — save notes
- `yc-bench scratchpad append --content "text"` — append notes

## Key Mechanics

- **Salary bumps**: completed tasks raise salary for every assigned employee. Assigning all 8 to every task compounds payroll until it exceeds revenue — assign 3-4 domain specialists instead.
- **Throughput split**: employees on multiple active tasks split their rate (rate/sqrt(N)). Two tasks run at ~71% each.
- **Deadlines**: success before deadline = reward + prestige. Failure = prestige penalty, no reward.
- **Trust**: completing tasks for a client builds trust → less work per task, access to gated tasks. Working for one client erodes trust with others.
- **Not all clients are reliable.** Check `client history` for failure patterns.
- **Payroll**: deducted monthly. Funds < 0 = bankruptcy.
- Prestige grows per domain. Higher prestige unlocks better-paying tasks.
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
    scratchpad: str | None = None,
) -> str:
    """Build per-turn context message injected as user input."""
    runway_months = (
        round(funds_cents / monthly_payroll_cents, 1)
        if monthly_payroll_cents > 0
        else None
    )
    runway_str = f"~{runway_months} months" if runway_months is not None else "∞ (no payroll)"

    history_limit = 20
    turns_until_truncation = max(0, history_limit - turn_number)

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
        f"- **Memory**: oldest messages drop after turn 20 ({turns_until_truncation} turns left). Use scratchpad to save important observations.",
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
                margin = ev.get("deadline_margin", "")
                margin_str = f" [{margin}]" if margin else ""
                n_emp = ev.get("employees_assigned", 0)
                bump = ev.get("salary_bump_total_cents", 0)
                bump_str = f" | {n_emp} employees, +${bump/100:,.0f}/mo payroll" if bump > 0 else f" | {n_emp} employees" if n_emp else ""
                if success:
                    parts.append(f"- {title}{client_str}: SUCCESS{funds_str}{margin_str}{bump_str}")
                else:
                    parts.append(f"- {title}{client_str}: FAILED — missed deadline{margin_str}, no reward")
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

    # Scratchpad is injected in the system prompt, not here (avoids duplication
    # across the 20-turn history window).

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
    scratchpad: str | None = None,
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
            "Check your scratchpad notes for strategy from the previous episode.",
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
        "2. `yc-bench task accept --task-id Task-42` — accept a task",
        "3. `yc-bench task assign-all --task-id Task-42` — assign employees (or use `task assign` to pick individuals)",
        "4. `yc-bench task dispatch --task-id Task-42` — start work",
        "5. `yc-bench sim resume` — advance time",
        "",
        "**IMPORTANT**: Check each command's result before proceeding to the next.",
        "If `task accept` fails (trust or prestige too low), try a different task.",
        "Do NOT call `sim resume` unless you have at least one active task — it will skip forward with zero revenue.",
    ])
    if bankrupt:
        lines.append("WARNING: company is already bankrupt at initialization.")
    return "\n".join(lines)


__all__ = ["SYSTEM_PROMPT", "build_turn_context", "build_initial_user_prompt"]
