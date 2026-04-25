from __future__ import annotations

import shlex


def parse_bench_command(command):
    if not isinstance(command, str) or not command.strip():
        return False, "command must be a non-empty string", None

    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return False, f"invalid command syntax: {command}", None

    if not argv:
        return False, "command must be a non-empty string", None

    if argv[0] != "yc-bench":
        return False, "only top-level `yc-bench` commands are allowed", None

    return True, None, argv


__all__ = ["parse_bench_command"]
