"""Run state: tracks the progress and terminal status of a benchmark run."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class TerminalReason(str, Enum):
    BANKRUPTCY = "bankruptcy"
    HORIZON_END = "horizon_end"
    ERROR = "error"


@dataclass
class TranscriptEntry:
    turn: int
    timestamp: str
    user_input: str
    agent_output: str
    commands_executed: List[str] = field(default_factory=list)


@dataclass
class RunState:
    """Mutable state for a single benchmark run."""

    session_id: str
    seed: int
    model: str
    horizon_years: int

    turn_count: int = 0
    terminal: bool = False
    terminal_reason: Optional[TerminalReason] = None
    terminal_detail: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    transcript: List[TranscriptEntry] = field(default_factory=list)
    next_user_input: Optional[str] = None
    total_cost_usd: float = 0.0

    # Multi-episode tracking
    current_episode: int = 1
    episode_results: List[Dict[str, Any]] = field(default_factory=list)

    def start(self) -> None:
        self.started_at = datetime.now(timezone.utc).isoformat()

    def record_turn(self, user_input: str, agent_output: str, commands_executed: List[str] | None = None, turn_cost_usd: float = 0.0) -> None:
        self.turn_count += 1
        self.total_cost_usd += turn_cost_usd
        self.transcript.append(TranscriptEntry(
            turn=self.turn_count,
            timestamp=datetime.now(timezone.utc).isoformat(),
            user_input=user_input,
            agent_output=agent_output,
            commands_executed=commands_executed or [],
        ))

    def mark_terminal(self, reason: TerminalReason, detail: str = "") -> None:
        self.terminal = True
        self.terminal_reason = reason
        self.terminal_detail = detail
        self.ended_at = datetime.now(timezone.utc).isoformat()

    def should_stop(self) -> bool:
        if self.terminal:
            return True
        return False

    def finish_episode(self) -> Dict[str, Any]:
        """Snapshot current episode state into episode_results and return it."""
        episode_data = {
            "episode": self.current_episode,
            "turns_completed": self.turn_count,
            "terminal_reason": self.terminal_reason.value if self.terminal_reason else None,
            "terminal_detail": self.terminal_detail,
            "cost_usd": round(self.total_cost_usd, 6),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "transcript": [
                {
                    "turn": t.turn,
                    "timestamp": t.timestamp,
                    "user_input": t.user_input,
                    "agent_output": t.agent_output,
                    "commands_executed": t.commands_executed,
                }
                for t in self.transcript
            ],
        }
        self.episode_results.append(episode_data)
        return episode_data

    def reset_for_new_episode(self) -> None:
        """Reset mutable state for a new episode, preserving episode_results."""
        self.current_episode += 1
        self.turn_count = 0
        self.terminal = False
        self.terminal_reason = None
        self.terminal_detail = None
        self.started_at = None
        self.ended_at = None
        self.transcript = []
        self.next_user_input = None
        self.total_cost_usd = 0.0

    def full_rollout(self) -> Dict[str, Any]:
        """Full results including transcript for saving to disk."""
        base = {
            "session_id": self.session_id,
            "model": self.model,
            "seed": self.seed,
            "horizon_years": self.horizon_years,
            "total_episodes": self.current_episode,
            "terminal": self.terminal,
            "terminal_reason": self.terminal_reason.value if self.terminal_reason else None,
            "terminal_detail": self.terminal_detail,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }
        if self.episode_results:
            # Multi-episode: include all episode data
            total_turns = sum(ep["turns_completed"] for ep in self.episode_results)
            total_cost = sum(ep["cost_usd"] for ep in self.episode_results)
            base["turns_completed"] = total_turns
            base["total_cost_usd"] = round(total_cost, 6)
            base["episodes"] = self.episode_results
        else:
            # Single-episode (backward compat): flat transcript
            base["turns_completed"] = self.turn_count
            base["total_cost_usd"] = round(self.total_cost_usd, 6)
            base["transcript"] = [
                {
                    "turn": t.turn,
                    "timestamp": t.timestamp,
                    "user_input": t.user_input,
                    "agent_output": t.agent_output,
                    "commands_executed": t.commands_executed,
                }
                for t in self.transcript
            ]
        return base

    def summary(self) -> Dict[str, Any]:
        """Summary without transcript for logging."""
        rollout = self.full_rollout()
        rollout.pop("transcript", None)
        if "episodes" in rollout:
            for ep in rollout["episodes"]:
                ep.pop("transcript", None)
        return rollout


__all__ = ["TerminalReason", "TranscriptEntry", "RunState"]
