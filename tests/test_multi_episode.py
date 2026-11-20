"""Multi-episode runs must start each episode with a fresh conversation.

Runs the real run_benchmark loop (real SQLite DB, real world seeding) with a
stub runtime and a stub agent loop, so no model is called.

    uv run python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from yc_bench.agent.run_state import TerminalReason
from yc_bench.runner import main as runner_main
from yc_bench.runner.args import RunArgs


class _StubRuntime:
    """Keeps one message list per session and records restores."""

    def __init__(self):
        self.messages = {}
        self.restored_from = []

    def clear_session(self, session_id):
        self.messages.pop(session_id, None)

    def save_session_messages(self, session_id, path):
        Path(path).write_text(json.dumps(self.messages.get(session_id, [])))

    def restore_session_messages(self, session_id, path):
        self.restored_from.append(Path(path).name)
        self.messages[session_id] = json.loads(Path(path).read_text())
        return len(self.messages[session_id])


class MultiEpisodeFreshStartTest(unittest.TestCase):
    def test_second_episode_does_not_resume_first_episode_chat(self):
        runtime = _StubRuntime()
        seen = {}

        def stub_loop(*, runtime, run_state, on_turn, episode, **_):
            seen[episode] = {
                "turn_count": run_state.turn_count,
                "messages": list(runtime.messages.get(run_state.session_id, [])),
            }
            runtime.messages[run_state.session_id] = [f"episode {episode} chat"]
            run_state.record_turn(f"episode {episode} prompt", "ok")
            on_turn({}, run_state, [])
            run_state.mark_terminal(TerminalReason.BANKRUPTCY, "stub")
            return run_state

        args = RunArgs(
            model="stub/model",
            seed=1,
            horizon_years=1,
            company_name="BenchCo",
            start_date="2025-01-01",
            no_live=True,
            max_episodes=2,
        )

        old_cwd = os.getcwd()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            os.chdir(tmp)
            try:
                with (
                    mock.patch.dict(os.environ),
                    mock.patch.object(
                        runner_main, "build_runtime", return_value=runtime
                    ),
                    mock.patch.object(
                        runner_main, "run_agent_loop", side_effect=stub_loop
                    ),
                ):
                    runner_main.run_benchmark(args)
            finally:
                os.chdir(old_cwd)

        self.assertEqual(sorted(seen), [1, 2])
        # Episode 2 starts at turn 0, so the loop shows the episode 2 opening prompt.
        self.assertEqual(seen[2]["turn_count"], 0)
        # And it sees none of episode 1's conversation.
        self.assertEqual(seen[2]["messages"], [])
        self.assertEqual(runtime.restored_from, [])


if __name__ == "__main__":
    unittest.main()
