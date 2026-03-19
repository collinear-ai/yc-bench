from __future__ import annotations

from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class RuntimeSettings:
    model: str
    temperature: float
    top_p: float
    request_timeout_seconds: float = 300.0
    retry_max_attempts: int = 3
    retry_backoff_seconds: float = 1.0
    history_keep_rounds: int = 20
    # Optional system prompt override; None = use default from agent/prompt.py
    system_prompt: str | None = None

@dataclass(frozen=True)
class RuntimeTurnRequest:
    session_id: str
    user_input: str
    scratchpad: str | None = None

@dataclass(frozen=True)
class RuntimeTurnResult:
    final_output: str
    raw_result: Any
    checkpoint_advanced: bool = False
    resume_payload: dict | None = None
    turn_cost_usd: float = 0.0

@dataclass(frozen=True)
class CommandResult:
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    sim_time: str | None
    command: str

__all__ = ["RuntimeSettings", "RuntimeTurnRequest", "RuntimeTurnResult", "CommandResult"]
