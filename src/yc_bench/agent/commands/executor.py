from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .policy import parse_bench_command


def _resolve_yc_bench() -> str:
    """Find the yc-bench script in the same directory as the running Python."""
    venv_bin = Path(sys.executable).parent
    candidate = venv_bin / "yc-bench"
    if candidate.exists():
        return str(candidate)
    return "yc-bench"  # fallback to PATH lookup


def run_command(
    command,
    *,
    cwd=None,
    timeout_seconds=60.0,
    command_exists=None,
):
    ok, err, argv = parse_bench_command(command)
    if not ok:
        return {
            "ok": False,
            "exit_code": 2,
            "stdout": "",
            "stderr": err or "invalid command",
            "sim_time": None,
            "command": command if isinstance(command, str) else str(command),
        }

    if command_exists is not None and not command_exists(argv):
        return {
            "ok": False,
            "exit_code": 127,
            "stdout": "",
            "stderr": f"command not found: {' '.join(argv)}",
            "sim_time": None,
            "command": command,
        }

    # Resolve yc-bench to the venv-local script
    if argv[0] == "yc-bench":
        argv[0] = _resolve_yc_bench()

    try:
        proc = subprocess.run(
            argv,
            shell=False,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "sim_time": None,
            "command": command,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "exit_code": 124,
            "stdout": exc.stdout or "",
            "stderr": f"command timed out after {timeout_seconds} seconds",
            "sim_time": None,
            "command": command,
        }
    except Exception as exc:
        return {
            "ok": False,
            "exit_code": 1,
            "stdout": "",
            "stderr": str(exc),
            "sim_time": None,
            "command": command,
        }


__all__ = ["run_command"]
