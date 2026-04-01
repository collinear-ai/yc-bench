from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RunCommandResult:
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    sim_time: str | None
    command: str


def normalize_result(payload):
    return RunCommandResult(
        ok=bool(payload.get("ok", False)),
        exit_code=int(payload.get("exit_code", 1)),
        stdout=str(payload.get("stdout", "")),
        stderr=str(payload.get("stderr", "")),
        sim_time=payload.get("sim_time"),
        command=str(payload.get("command", "")),
    )
