from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field

import litellm

from .base import AgentRuntime
from .schemas import RuntimeTurnResult
from ..prompt import SYSTEM_PROMPT
from ..tools.run_command_schema import normalize_result

logger = logging.getLogger(__name__)

litellm.suppress_debug_info = True

# Tool schema passed to the LLM on every call
_RUN_COMMAND_TOOL = {
    "type": "function",
    "function": {
        "name": "run_command",
        "description": (
            "Execute one benchmark CLI command inside the sandbox "
            "and return structured execution output."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The full yc-bench CLI command to execute.",
                }
            },
            "required": ["command"],
        },
    },
}


@dataclass
class _Session:
    messages: list = field(default_factory=list)


class LiteLLMRuntime(AgentRuntime):

    def __init__(self, settings, command_executor):
        self._settings = settings
        self._command_executor = command_executor
        self._sessions: dict[str, _Session] = {}

        self._request_timeout_seconds = settings.request_timeout_seconds
        self._retry_max_attempts = settings.retry_max_attempts
        self._retry_backoff_seconds = settings.retry_backoff_seconds

        if self._request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be > 0")
        if self._retry_max_attempts <= 0:
            raise ValueError("retry_max_attempts must be > 0")
        if self._retry_backoff_seconds <= 0:
            raise ValueError("retry_backoff_seconds must be > 0")

        # API key: check provider-specific env vars, then generic fallbacks.
        # LiteLLM reads these natively for their respective providers, but we
        # also pass the key explicitly via kwargs to be safe.
        self._api_key = (
            os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("OPENROUTER_API_KEY")
            or None
        )
        # Base URL: only needed for raw OpenAI-compatible endpoints.
        # openrouter/ model prefix is handled natively by LiteLLM without this.
        self._api_base = os.environ.get("OPENAI_BASE_URL") or None

        self._history_keep_rounds = settings.history_keep_rounds

        logger.info(
            "LiteLLMRuntime configured: model=%s api_base=%s history_keep_rounds=%d",
            self._settings.model,
            self._api_base or "default",
            self._history_keep_rounds,
        )

    # ------------------------------------------------------------------
    # AgentRuntime interface
    # ------------------------------------------------------------------

    def run_turn(self, request):
        session = self._get_or_create_session(request.session_id)
        # Proactively drop old rounds before appending new input.
        self._proactive_truncate(session)
        session.messages.append({"role": "user", "content": request.user_input})

        result = None
        last_err = None

        for attempt in range(1, self._retry_max_attempts + 1):
            try:
                result = self._run_with_timeout(session)
                break
            except Exception as e:
                last_err = e
                if self._is_context_length_error(e):
                    logger.warning(
                        "Context-length error on attempt %d despite proactive truncation "
                        "(history_keep_rounds=%d). Consider reducing YC_BENCH_HISTORY_KEEP_ROUNDS.",
                        attempt,
                        self._history_keep_rounds,
                    )
                    continue
                logger.warning("Turn attempt %d failed: %s", attempt, e)
                if attempt >= self._retry_max_attempts:
                    raise RuntimeError(
                        f"Failed to run turn after {self._retry_max_attempts} attempts"
                    ) from last_err
                time.sleep(self._retry_backoff_seconds * (2 ** (attempt - 1)))

        if result is None:
            raise RuntimeError("run_turn failed without result") from last_err

        final_output, tool_calls_made, resume_payload, turn_cost = result
        return RuntimeTurnResult(
            final_output=final_output,
            raw_result={"tool_calls": tool_calls_made},
            checkpoint_advanced=resume_payload is not None,
            resume_payload=resume_payload,
            turn_cost_usd=turn_cost,
        )

    def clear_session(self, session_id):
        self._sessions.pop(session_id, None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_with_timeout(self, session):
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self._do_turn, session)
            try:
                return future.result(timeout=self._request_timeout_seconds)
            except FuturesTimeoutError as exc:
                future.cancel()
                raise TimeoutError(
                    f"LiteLLM call timed out after {self._request_timeout_seconds}s"
                ) from exc

    def _do_turn(self, session):
        """One LLM call + tool execution. Returns (final_output, tool_calls_made, resume_payload, cost_usd)."""
        system_prompt = self._settings.system_prompt or SYSTEM_PROMPT
        messages = [{"role": "system", "content": system_prompt}] + session.messages

        kwargs = dict(
            model=self._settings.model,
            messages=messages,
            tools=[_RUN_COMMAND_TOOL],
            tool_choice="auto",
            timeout=self._request_timeout_seconds,
        )
        if self._api_base:
            kwargs["api_base"] = self._api_base
        # Let LiteLLM resolve API keys from provider-specific env vars
        # (ANTHROPIC_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY, etc.)
        # rather than passing a single key that may not match the provider.

        response = litellm.completion(**kwargs)

        # Log token usage and cost per call
        turn_cost = 0.0
        usage = getattr(response, "usage", None)
        if usage:
            cost = getattr(response, "_hidden_params", {}).get("response_cost") or 0
            turn_cost = float(cost)
            logger.info(
                "LLM call: prompt_tokens=%s completion_tokens=%s cost=$%.6f",
                getattr(usage, "prompt_tokens", "?"),
                getattr(usage, "completion_tokens", "?"),
                turn_cost,
            )

        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None) or []

        tool_calls_made = []
        resume_payload = None

        if tool_calls:
            # Persist assistant message with tool calls
            session.messages.append({
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ],
            })

            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                    command = args.get("command", "")
                except Exception:
                    command = ""

                raw = self._command_executor(command)
                normalized = normalize_result(raw)
                tool_result_str = json.dumps(normalized.__dict__)

                tool_calls_made.append({"command": command, "result": tool_result_str})

                # Extract resume payload when the agent advances simulation time
                if command.startswith("yc-bench sim resume"):
                    try:
                        stdout = normalized.__dict__.get("stdout", "")
                        if isinstance(stdout, str) and stdout.strip():
                            payload = json.loads(stdout)
                            if isinstance(payload, dict):
                                resume_payload = payload
                    except Exception:
                        pass

                session.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result_str,
                })

            cmds = [tc["command"] for tc in tool_calls_made]
            final_output = f"Executed {len(tool_calls)} tool call(s): {', '.join(cmds)}"
        else:
            content = message.content or ""
            session.messages.append({"role": "assistant", "content": content})
            final_output = content

        return final_output, tool_calls_made, resume_payload, turn_cost

    def _get_or_create_session(self, session_id: str) -> _Session:
        if session_id not in self._sessions:
            self._sessions[session_id] = _Session()
        return self._sessions[session_id]

    def _is_context_length_error(self, err: Exception) -> bool:
        text = str(err).lower()
        patterns = (
            "context length",
            "maximum context",
            "max context",
            "too many tokens",
            "prompt is too long",
            "token limit",
            "context window",
        )
        return any(p in text for p in patterns)

    def _round_start_indices(self, messages: list) -> list[int]:
        """Return indices of user messages — each marks the start of a round."""
        return [i for i, m in enumerate(messages) if m.get("role") == "user"]

    def _proactive_truncate(self, session: _Session) -> None:
        """Drop oldest rounds before each turn so at most history_keep_rounds remain."""
        messages = session.messages
        user_indices = self._round_start_indices(messages)
        if len(user_indices) <= self._history_keep_rounds:
            return
        cutoff = user_indices[-self._history_keep_rounds]
        marker = {
            "role": "user",
            "content": (
                f"[Earlier turns removed. Only the last {self._history_keep_rounds} "
                "turns are retained in this context window.]"
            ),
        }
        session.messages = [marker] + messages[cutoff:]
        logger.info(
            "Proactive truncation: kept last %d rounds (%d → %d messages).",
            self._history_keep_rounds,
            len(messages),
            len(session.messages),
        )


__all__ = ["LiteLLMRuntime"]
