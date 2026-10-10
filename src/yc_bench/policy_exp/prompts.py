"""Prompt templates for policy code generation."""

from __future__ import annotations

POLICY_SYSTEM_PROMPT = """\
You are writing a deterministic Python policy that operates a simulated
AI startup over a one-year horizon. The simulation is driven entirely
through a CLI (`yc-bench`). Your goal: maximize the company's funds
while avoiding bankruptcy.

You are not invoked turn-by-turn. You write a single Python function
ONCE; it is then executed against the simulator and your final score
is the company's funds at horizon end (or 0 if you went bankrupt).

You have a fixed cheap LLM helper (`env.classify`) you may consult
sparingly for NLP-flavoured judgement. Everything else must be
deterministic Python.

Output format: a single Python code block with `def run(env): ...`
at module top level. No prose, no other code blocks.
"""


# The CLI doc is copied/condensed from src/yc_bench/agent/prompt.py so
# generated policies see the same surface as the agent baseline.
CLI_DOC = """\
## yc-bench CLI

All commands return JSON on stdout. Use `env.run_command(cmd)` to invoke.
The returned dict has:
  ok (bool), exit_code (int), stdout (str), stderr (str),
  parsed (dict|list|None — JSON-parsed stdout, or None if not JSON)

### Observe
- `yc-bench company status` — funds_cents, prestige_levels, payroll
- `yc-bench employee list` — employees with per-domain skill rates
- `yc-bench market browse [--domain X] [--reward-min-cents N] [--limit N]`
- `yc-bench task list [--status active|planned|completed_success|completed_fail]`
- `yc-bench task inspect --task-id Task-42`
- `yc-bench client list` — clients with trust levels
- `yc-bench client history` — per-client success/failure counts
- `yc-bench finance ledger`

### Act
- `yc-bench task accept --task-id Task-42`
- `yc-bench task assign --task-id Task-42 --employees Emp_1,Emp_4,Emp_7`
  (This is the ONLY way to assign employees. There is no `assign-all`.)
- `yc-bench task dispatch --task-id Task-42`
  (Fails with exit_code 1 if no employees are assigned to the task.)
- `yc-bench task cancel --task-id Task-42 --reason "text"`
- `yc-bench sim resume`     # advance simulation clock to next event
- `yc-bench scratchpad write --content "..."`   # optional, persists to DB

### Key mechanics
- Funds < 0 = bankruptcy (terminal). Horizon end = ~1 year of sim time.
- Work happens during weekday business hours. Payroll deducted monthly.
- Completed tasks bump every assigned employee's salary by ~1% — assigning
  many employees to small tasks compounds payroll growth dangerously.
- Employees on N active tasks split throughput rate/N per task.
- A subset (~35%) of clients are adversarial: after acceptance they inflate
  task work, causing deadline failures. Their advertised rewards are high.
  Adversarial status is hidden — infer from `client history` failure rates.
- Trust with a client builds via successful completions and reduces
  required work; it decays for *other* clients when you focus on one.
- Higher prestige unlocks higher-reward tasks but also raises required qty.
"""


ENV_DOC = """\
## env API (the only object your policy receives)

```python
def run(env):
    # env.run_command(cmd: str) -> dict
    #   See CLI doc for the dict shape. Use env.run_command("yc-bench ...").
    #
    # env.classify(prompt: str, options: list[str] | None = None,
    #              system: str | None = None, max_tokens: int = 200) -> str
    #   Delegates ONE classification to a fixed cheap LLM that is shared
    #   across all policies in this experiment. If `options` is provided,
    #   the return value is guaranteed to be one of those options.
    #   Use sparingly — there is a per-episode budget.
    #
    # env.done            # bool — flips True after sim resume hits bankruptcy/horizon
    # env.last_resume     # the last sim-resume payload (dict | None)
    # env.terminal_reason # "horizon_end" | "bankruptcy" | None
    # env.turn            # number of run_command calls so far
    # env.helper_calls    # number of classify() calls so far

    while not env.done:
        ...
        env.run_command("yc-bench sim resume")
```

Hard constraints:
- You may import only the Python standard library. No network, no extra LLM
  calls outside `env.classify`. The simulation is purely local.
- You MUST call `yc-bench sim resume` repeatedly to advance time. Without it,
  no work happens, no revenue arrives, and the run will time out.
- Do NOT call `sim resume` when you have zero active tasks — payroll will
  drain your runway with no revenue. Always have at least one accepted +
  assigned + dispatched task before resuming.
- Your policy must terminate (return) when `env.done` is True. The harness
  will also stop you if you exceed budgets.
"""


HELPER_USAGE_HINTS = """\
## Useful patterns for env.classify

You can use the helper to interpret stringy data the CLI returns. Some
examples (you don't have to use any of these — invent your own):

- After several `client history` snapshots, ask the helper which client
  names look adversarial:
      pick = env.classify(
          "Which client looks adversarial? Their failure rates: ...",
          options=[c["name"] for c in clients],
      )

- Ask the helper to score a market task's reward/effort tradeoff if you
  want a heuristic you'd rather not hand-code.

Each call costs latency. Cache the answer in a Python dict so you only
classify once per (client, evidence-window).
"""


def build_codegen_messages(extra_hint: str | None = None) -> list[dict]:
    """Construct the messages array for codegen."""
    user = "\n\n".join(
        [
            CLI_DOC,
            ENV_DOC,
            HELPER_USAGE_HINTS,
            (extra_hint or ""),
            (
                "Now write the policy. Output a SINGLE Python code block "
                "containing `def run(env):` and any helpers it needs at "
                "module level. No prose outside the code block."
            ),
        ]
    )
    return [
        {"role": "system", "content": POLICY_SYSTEM_PROMPT},
        {"role": "user", "content": user.strip()},
    ]


ITERATE_INTRO = """\
You are revising a policy you (or an earlier iteration) wrote for the
yc-bench startup simulation. Below is a record of how prior iterations
performed against a fixed seed. Use this as ground truth: study the
failure modes, fix them, keep what worked. Output a *complete* new
policy — not a diff.

Rules of thumb when reading the history:
- `final_funds_cents` is the score. It starts at $200,000.
- `bankrupt: True` and `terminal_reason: bankruptcy` mean payroll
  outpaced revenue.
- `terminal_reason: horizon_end` means you survived a full year — try
  to grow funds beyond the start.
- `task_status_counts` shows whether tasks ever completed. All-`planned`
  means the policy never managed to make tasks `active` (usually a CLI
  syntax bug — check `error_codes` for which command failed).
- `error_codes` shows the verb -> count of non-zero exit codes. A
  command failing repeatedly means you used an option/flag that does
  not exist OR the precondition wasn't met.
- `last_commands` shows the very last sequence before terminal — useful
  for diagnosing the death loop.

Diagnostic fields (use these to understand WHY the run ended that way):

- `monthly_timeline` — one row per sim-month with revenue / payroll /
  net delta / running funds. The month where `net_delta_cents` first
  goes deeply negative is the failure point. If `revenue_cents` stays
  near 0 for multiple months while `payroll_cents` keeps deducting,
  no tasks are completing — the policy is accepting but not finishing.

- `task_outcomes.by_status` — count of tasks per terminal status.
  `completed_success` is good; `completed_fail` (deadline missed) or
  `canceled` is wasted work. Lots of `planned` at end means tasks
  accumulated without dispatch. `sample_failed_tasks` lists clients
  whose tasks failed deadlines — if the same client name appears
  repeatedly, that client may be adversarial.

- `payroll_growth.growth_factor` — final monthly payroll divided by
  initial. If this is much greater than 1, salary bumps from many
  employees being assigned per task have compounded payroll faster
  than revenue could keep up. Aim to keep teams small.

- `concurrency.max_active` and `mean_active` — peak and average
  concurrent active tasks. If `mean_active < 1`, employees are idle
  most of the time. If `max_active` is large but `task_outcomes` has
  many `completed_fail`, you over-committed — throughput splits
  rate/N across active tasks per employee.
"""


def build_iterate_messages(
    history: list[dict],
    prev_code: str,
    extra_hint: str | None = None,
) -> list[dict]:
    """Construct messages for an iteration codegen call.

    `history` is a list of per-iteration summaries (most recent last).
    `prev_code` is the source of the most recent policy.
    """
    import json as _json

    history_json = _json.dumps(history, indent=2, default=str)
    user = "\n\n".join(
        [
            CLI_DOC,
            ENV_DOC,
            HELPER_USAGE_HINTS,
            ITERATE_INTRO,
            f"## Run history (most recent last)\n```json\n{history_json}\n```",
            f"## Your previous policy source\n```python\n{prev_code}\n```",
            (extra_hint or ""),
            (
                "Now write a REVISED policy that addresses the failure modes "
                "above. Output a SINGLE Python code block containing "
                "`def run(env):` and any helpers it needs at module level. "
                "No prose outside the code block."
            ),
        ]
    )
    return [
        {"role": "system", "content": POLICY_SYSTEM_PROMPT},
        {"role": "user", "content": user.strip()},
    ]


__all__ = [
    "POLICY_SYSTEM_PROMPT",
    "CLI_DOC",
    "ENV_DOC",
    "HELPER_USAGE_HINTS",
    "ITERATE_INTRO",
    "build_codegen_messages",
    "build_iterate_messages",
]
