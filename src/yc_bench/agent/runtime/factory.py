from __future__ import annotations

from .base import AgentRuntime
from .litellm_runtime import LiteLLMRuntime
from .schemas import RuntimeSettings


def build_runtime(settings, command_executor):
    return LiteLLMRuntime(
        settings=settings,
        command_executor=command_executor,
    )


__all__ = ["build_runtime"]
