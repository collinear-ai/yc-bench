from __future__ import annotations

import json
import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db.models.company import Company
from ..db.models.employee import Employee
from ..db.models.sim_state import SimState
from ..db.models.task import Task, TaskStatus
from .prompt import build_initial_user_prompt, build_turn_context
from .run_state import RunState, TerminalReason
from .runtime.base import AgentRuntime
from .runtime.schemas import RuntimeTurnRequest

logger = logging.getLogger(__name__)


def _snapshot_state(db: Session, company_id):
    """Read current simulation state from DB for context building."""
    sim_state = db.query(SimState).filter(SimState.company_id == company_id).one()
    company = db.query(Company).filter(Company.id == company_id).one()

    active_count = db.query(func.count(Task.id)).filter(
        Task.company_id == company_id,
        Task.status == TaskStatus.ACTIVE,
    ).scalar() or 0

    planned_count = db.query(func.count(Task.id)).filter(
        Task.company_id == company_id,
        Task.status == TaskStatus.PLANNED,
    ).scalar() or 0

    employee_count = db.query(func.count(Employee.id)).filter(
        Employee.company_id == company_id,
    ).scalar() or 0

    monthly_payroll = db.query(func.sum(Employee.salary_cents)).filter(
        Employee.company_id == company_id,
    ).scalar() or 0

    # Read scratchpad if it exists
    from ..db.models.scratchpad import Scratchpad
    scratchpad = db.query(Scratchpad).filter(Scratchpad.company_id == company_id).one_or_none()
    scratchpad_content = scratchpad.content if scratchpad and scratchpad.content else None

    return {
        "sim_time": sim_state.sim_time.isoformat(),
        "horizon_end": sim_state.horizon_end.isoformat(),
        "funds_cents": company.funds_cents,
        "active_tasks": active_count,
        "planned_tasks": planned_count,
        "employee_count": employee_count,
        "monthly_payroll_cents": int(monthly_payroll),
        "bankrupt": company.funds_cents < 0,
        "scratchpad": scratchpad_content,
    }


def _extract_commands(raw_result) -> list[str]:
    """Extract CLI commands the agent executed from the raw_result dict.

    raw_result is {"tool_calls": [{"command": "yc-bench ...", "result": "..."}, ...]}.
    """
    commands = []
    try:
        for tc in (raw_result or {}).get("tool_calls", []):
            cmd = tc.get("command", "")
            result = tc.get("result", "")
            if cmd:
                commands.append(f"{cmd} -> {result[:500]}" if result else cmd)
    except Exception as exc:
        logger.debug("Could not extract commands from raw_result: %s", exc)
    return commands


def _auto_resume(command_executor) -> tuple[dict | None, str | None]:
    """Force-advance simulation time via sim resume. Returns (payload, error_msg)."""
    try:
        raw = command_executor("yc-bench sim resume")
        stdout = raw.get("stdout", "")
        if isinstance(stdout, str) and stdout.strip():
            payload = json.loads(stdout)
            if isinstance(payload, dict):
                return payload, None
        return None, raw.get("stderr", "sim resume returned no payload")
    except Exception as exc:
        return None, str(exc)


def _build_resume_handoff_user_input(payload: dict) -> str:
    """Build next-turn user message from sim resume payload."""
    wake_events = payload.get("wake_events") or []
    lines = [
        "Simulation advanced via `yc-bench sim resume`.",
        f"- old_sim_time: {payload.get('old_sim_time')}",
        f"- new_sim_time: {payload.get('new_sim_time')}",
        f"- checkpoint_event_type: {payload.get('checkpoint_event_type')}",
        f"- events_processed: {payload.get('events_processed')}",
        f"- payrolls_applied: {payload.get('payrolls_applied')}",
        f"- balance_delta: {payload.get('balance_delta')}",
        f"- bankrupt: {payload.get('bankrupt')}",
        f"- horizon_reached: {payload.get('horizon_reached')}",
        f"- terminal_reason: {payload.get('terminal_reason')}",
        f"- wake_events_count: {len(wake_events)}",
    ]
    for ev in wake_events:
        lines.append(f"- wake_event: {ev}")
    lines.append(
        "You are now at the new checkpoint. Query live state via yc-bench commands and decide next actions."
    )
    return "\n".join(lines)


def run_agent_loop(
    runtime: AgentRuntime,
    db_factory,
    company_id,
    run_state: RunState,
    command_executor=None,
    auto_advance_after_turns: int = 10,
    max_turns: int | None = None,
    on_turn_start=None,
    on_turn=None,
    episode: int = 1,
) -> RunState:
    run_state.start()
    turns_since_resume = 0  # consecutive turns without sim resume

    logger.info(
        "Starting agent loop: model=%s seed=%d auto_advance_after=%d turns max_turns=%s",
        run_state.model, run_state.seed, auto_advance_after_turns, max_turns or "unlimited",
    )

    while not run_state.terminal:
        if max_turns is not None and run_state.turn_count >= max_turns:
            logger.info("Reached max_turns=%d, stopping.", max_turns)
            run_state.mark_terminal(TerminalReason.ERROR, f"max_turns={max_turns} reached")
            break
        turn_num = run_state.turn_count + 1
        if run_state.turn_count == 0:
            with db_factory() as db:
                snapshot = _snapshot_state(db, company_id)
            user_input = build_initial_user_prompt(**snapshot, episode=episode)
        elif run_state.next_user_input is not None:
            user_input = run_state.next_user_input
            run_state.next_user_input = None
        else:
            with db_factory() as db:
                snapshot = _snapshot_state(db, company_id)
            user_input = build_turn_context(
                turn_number=turn_num,
                **snapshot,
            )

        if on_turn_start is not None:
            on_turn_start(turn_num)

        try:
            result = runtime.run_turn(
                RuntimeTurnRequest(
                    session_id=run_state.session_id,
                    user_input=user_input,
                    scratchpad=snapshot.get("scratchpad"),
                )
            )
            agent_output = result.final_output
        except Exception as exc:
            logger.error("Runtime error on turn %d: %s", turn_num, exc)
            run_state.mark_terminal(TerminalReason.ERROR, str(exc))
            break

        commands_executed = _extract_commands(result.raw_result)

        resume_payload = result.resume_payload
        if result.checkpoint_advanced and resume_payload is not None:
            logger.info("Turn %d: agent called sim resume.", turn_num)
            turns_since_resume = 0
        else:
            turns_since_resume += 1
            if command_executor is not None and turns_since_resume >= auto_advance_after_turns:
                logger.info(
                    "Turn %d: %d consecutive turns without sim resume; auto-advancing.",
                    turn_num, turns_since_resume,
                )
                resume_payload, err = _auto_resume(command_executor)
                if err:
                    logger.warning("Auto-resume failed on turn %d: %s", turn_num, err)
                else:
                    turns_since_resume = 0

        if resume_payload is not None:
            # Query full state so the agent sees active/planned task counts
            # and gets the "ACTION REQUIRED" nudge when idle.
            wake_events = resume_payload.get("wake_events") or []
            with db_factory() as db:
                post_resume_snapshot = _snapshot_state(db, company_id)
            run_state.next_user_input = build_turn_context(
                turn_number=run_state.turn_count + 1,
                **post_resume_snapshot,
                last_wake_events=wake_events,
            )
            reason = resume_payload.get("terminal_reason")
            if reason == "bankruptcy":
                run_state.mark_terminal(TerminalReason.BANKRUPTCY, reason)
            elif reason == "horizon_end":
                run_state.mark_terminal(TerminalReason.HORIZON_END, reason)
            if run_state.terminal:
                logger.info("Terminal after turn %d: %s", turn_num, reason)

        run_state.record_turn(
            user_input=user_input,
            agent_output=agent_output,
            commands_executed=commands_executed,
            turn_cost_usd=getattr(result, "turn_cost_usd", 0.0),
        )

        if on_turn is not None:
            with db_factory() as db:
                post_snapshot = _snapshot_state(db, company_id)
            on_turn(post_snapshot, run_state, commands_executed)

        logger.info(
            "Turn %d complete. Agent output length: %d, commands: %d",
            turn_num, len(agent_output), len(commands_executed),
        )

    logger.info(
        "Agent loop finished: turns=%d terminal=%s reason=%s",
        run_state.turn_count,
        run_state.terminal,
        run_state.terminal_reason,
    )

    return run_state


__all__ = ["run_agent_loop"]
