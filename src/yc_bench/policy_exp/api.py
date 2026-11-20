"""Runtime environment exposed to a generated policy.

A policy is a Python function `run(env)` that drives the yc-bench CLI
through `env.run_command` and may call `env.classify` to delegate
NLP-flavoured judgement (e.g. "is this client adversarial?") to a
shared cheap LLM. The same helper is used for every model under test
so the variable is policy quality, not helper choice.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import litellm

from ..agent.commands.executor import run_command as _bench_run_command

logger = logging.getLogger(__name__)

litellm.suppress_debug_info = True
litellm.drop_params = True


DEFAULT_HELPER_MODEL = os.environ.get(
    "YC_BENCH_HELPER_MODEL", "openrouter/google/gemini-2.5-flash-lite"
)


class PolicyBudgetExceeded(RuntimeError):
    """Raised when a policy exceeds its command or classify budget."""


class PolicyTerminated(Exception):
    """Internal signal: simulation reached a terminal state. Policies may
    catch this to exit cleanly, but ignoring it is fine — `env.done`
    will be True and subsequent run_command calls become no-ops."""


@dataclass
class PolicyEnv:
    """The single object passed to a generated policy's `run(env)` function.

    Public surface (everything a generated policy is allowed to touch):

        env.run_command(cmd: str) -> dict   # shell out to yc-bench CLI
        env.classify(prompt, options=None, ...) -> str | dict
        env.done            # True once sim reached bankruptcy/horizon
        env.turn            # number of run_command calls so far
        env.helper_calls    # number of classify() calls so far
        env.last_resume     # the most recent sim resume payload (or None)
    """

    helper_model: str = DEFAULT_HELPER_MODEL
    max_commands: int = 5000
    max_helper_calls: int = 200
    raise_on_terminal: bool = False  # if True, run_command raises PolicyTerminated after sim end

    done: bool = False
    terminal_reason: str | None = None
    turn: int = 0
    helper_calls: int = 0
    last_resume: dict | None = None
    helper_log: list[dict] = field(default_factory=list)
    command_log: list[dict] = field(default_factory=list)

    # ------------------------------------------------------------------
    # CLI bridge
    # ------------------------------------------------------------------

    def run_command(self, command: str) -> dict[str, Any]:
        """Execute one yc-bench CLI command. Returns the executor dict
        with an extra `parsed` field when stdout is JSON.

        After `sim resume` reports a terminal state, `env.done` flips True.
        Further calls are still permitted (so a policy's loop can exit
        cleanly), but they will return the cached terminal payload."""
        if self.done and command.strip().startswith("yc-bench sim resume"):
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": json.dumps(self.last_resume or {}),
                "stderr": "",
                "parsed": self.last_resume,
                "command": command,
                "terminal": True,
            }

        if self.turn >= self.max_commands:
            raise PolicyBudgetExceeded(
                f"command budget exhausted ({self.max_commands})"
            )
        self.turn += 1

        raw = _bench_run_command(command)
        parsed = None
        stdout = raw.get("stdout", "")
        if isinstance(stdout, str) and stdout.strip():
            try:
                parsed = json.loads(stdout)
            except Exception:
                parsed = None
        raw["parsed"] = parsed

        # Detect terminal state from sim resume payloads.
        if (
            command.strip().startswith("yc-bench sim resume")
            and isinstance(parsed, dict)
        ):
            self.last_resume = parsed
            reason = parsed.get("terminal_reason")
            if reason in ("bankruptcy", "horizon_end") or parsed.get("bankrupt") or parsed.get("horizon_reached"):
                self.done = True
                self.terminal_reason = reason or (
                    "bankruptcy" if parsed.get("bankrupt") else "horizon_end"
                )
                if self.raise_on_terminal:
                    raise PolicyTerminated(self.terminal_reason)

        self.command_log.append(
            {
                "turn": self.turn,
                "command": command,
                "ok": raw.get("ok"),
                "exit_code": raw.get("exit_code"),
            }
        )
        return raw

    # ------------------------------------------------------------------
    # Helper LLM
    # ------------------------------------------------------------------

    def classify(
        self,
        prompt: str,
        options: list[str] | None = None,
        *,
        system: str | None = None,
        max_tokens: int = 200,
    ) -> str:
        """Delegate one classification/extraction to the shared helper LLM.

        If `options` is provided the helper is instructed to return exactly
        one of them; the returned string is the chosen option (best-effort
        match). Otherwise free text is returned. All policies in the
        experiment share the same helper model and budget, so this measures
        the *policy author's* judgement about when to consult an LLM, not
        their ability to pick one.
        """
        if self.helper_calls >= self.max_helper_calls:
            raise PolicyBudgetExceeded(
                f"helper.classify budget exhausted ({self.max_helper_calls})"
            )
        self.helper_calls += 1

        sys_msg = system or (
            "You are a classification helper. Be concise. "
            "If options are listed, output exactly one option verbatim and nothing else."
        )
        if options:
            user_msg = (
                f"{prompt}\n\nRespond with exactly one of these options:\n"
                + "\n".join(f"- {o}" for o in options)
            )
        else:
            user_msg = prompt

        t0 = time.time()
        try:
            resp = litellm.completion(
                model=self.helper_model,
                messages=[
                    {"role": "system", "content": sys_msg},
                    {"role": "user", "content": user_msg},
                ],
                max_tokens=max_tokens,
                temperature=0.0,
                timeout=60.0,
            )
            content = (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            logger.warning("helper.classify failed: %s", exc)
            content = options[0] if options else ""

        if options:
            content = self._snap_to_option(content, options)

        self.helper_log.append(
            {
                "call": self.helper_calls,
                "prompt_chars": len(prompt),
                "options": options,
                "answer": content,
                "ms": int((time.time() - t0) * 1000),
            }
        )
        return content

    @staticmethod
    def _snap_to_option(answer: str, options: list[str]) -> str:
        """Best-effort: pick the option the helper most plausibly chose."""
        a = answer.strip().lower()
        for opt in options:
            if opt.lower() == a:
                return opt
        for opt in options:
            if opt.lower() in a or a in opt.lower():
                return opt
        return options[0]


__all__ = ["PolicyEnv", "PolicyBudgetExceeded", "PolicyTerminated", "DEFAULT_HELPER_MODEL"]
