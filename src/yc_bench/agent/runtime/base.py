from __future__ import annotations

from abc import ABC, abstractmethod


class AgentRuntime(ABC):
    @abstractmethod
    def run_turn(self, request):
        raise NotImplementedError

    @abstractmethod
    def clear_session(self, session_id):
        raise NotImplementedError

    def save_session_messages(self, session_id: str, path) -> None:
        """Persist session messages for crash recovery. Override in subclass."""
        pass

    def restore_session_messages(self, session_id: str, path) -> int:
        """Restore session messages from file. Returns count. Override in subclass."""
        return 0


__all__ = ["AgentRuntime"]
