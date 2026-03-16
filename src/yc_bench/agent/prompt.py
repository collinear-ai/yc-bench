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
- `yc-bench market browse [--domain X] [--required-prestige-lte N] [--reward-min-cents N] [--limit N] [--offset N]` — browse available tasks (default limit 50; the response includes a `total` field — if total > 50, paginate with --offset to see more). Tasks show `client_name` and `required_trust`.
- `yc-bench task list [--status X]` — list your tasks (planned, active, completed, cancelled)
- `yc-bench task inspect --task-id <UUID>` — detailed task info (requirements, assignments, progress, client, trust requirement)
- `yc-bench client list` — show all clients with current trust levels
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

## Key Rules

- Task completion at or before deadline = success (reward funds + prestige + skill boost + client trust gain)
- Task completion after deadline = failure (0.8x prestige penalty, no reward, trust penalty)
- Task cancellation = 1.2x prestige penalty per domain + trust penalty (worse than failure)
- Employee throughput = base_rate / number_of_active_tasks_assigned
- Time advances only when you run `yc-bench sim resume` — it jumps to the next event (task milestone at 25/50/75%, task completion, or monthly payroll). **Warning**: calling `sim resume` with no active tasks just skips to the next payroll, burning runway with zero revenue.
- Prestige is clamped [1, 10]. Funds are in cents.

## Client Trust

- Each task is offered by a specific **client** (e.g. "Nexus AI", "Vertex Labs").
- Each client has **specialty domains** (e.g. "research", "training"). Tasks from a client are biased toward their specialties.
- Use `yc-bench client list` to see each client's specialties and current trust level.

### Mechanics
- Completing tasks for a client builds **trust** [0.0–5.0]. Trust gains diminish as you approach max.
- Trusted clients require less work (up to 35% reduction at max trust).
- Some tasks require minimum trust to accept (required_trust 1-4).
- Trust decays daily. Task failure and cancellation reduce trust.
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
        "You MUST complete these steps now (multiple commands per turn are fine):",
        "1. `yc-bench company status` — check your current prestige levels",
        "2. `yc-bench market browse` — find tasks you can accept (use `--required-prestige-lte N` matching your prestige)",
        "3. `yc-bench task accept --task-id <UUID>` — accept 2-3 suitable tasks",
        "4. `yc-bench employee list` — get employee IDs",
        "5. `yc-bench task assign --task-id <UUID> --employee-id <UUID>` — assign employees",
        "6. `yc-bench task dispatch --task-id <UUID>` — start work on each assigned task",
        "7. `yc-bench sim resume` — advance time to collect the first task completion event",
        "",
        "Do not spend multiple turns just browsing. Accept and dispatch tasks immediately.",
    ])
    if bankrupt:
        lines.append("WARNING: company is already bankrupt at initialization.")
    return "\n".join(lines)


__all__ = ["SYSTEM_PROMPT", "build_turn_context", "build_initial_user_prompt"]
