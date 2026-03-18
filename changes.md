# Changes — Client Trust Redesign

## Core Feature: Hidden Client Loyalty

Each client gets a hidden loyalty score [-1, 1] at world generation. The agent can't see it.

- **Loyal** (> 0.3): Work reduction at high trust — tasks complete faster
- **Neutral** (-0.3 to 0.3): No special effects
- **RAT** (< -0.3): Adversarial — scope creep inflates work, causing deadline failures

### Scope Creep (the main RAT mechanic)

When the agent accepts a task from a RAT client, the required work is secretly inflated by `severity × |loyalty|`. The deadline is based on the ORIGINAL work amount. Result: RAT tasks miss deadlines → zero reward + prestige penalty.

Example (Vertex Labs, loyalty -0.65, severity 2.0):
- Normal task: 800 units, takes 2.8 days, deadline 6 days → OK
- Scope-creeped: 800 × 2.3 = 1840 units, takes 6.4 days, deadline 6 days → FAIL

RATs look identical to normal clients. The only way to detect them is noticing that tasks from certain clients fail disproportionately.

### How the Agent Detects RATs

1. Wake events include client name: "Task-42 (client: Vertex Labs): FAILED — missed deadline"
2. `yc-bench client history` shows per-client `failure_rate_pct`
3. The agent must notice the pattern and stop accepting tasks from that client

### Payment Disputes (disabled)

Built but disabled — scope creep alone creates sufficient damage. Can be re-enabled in `task_complete.py`.

## DB Schema Changes

- `Client.loyalty` — float, hidden loyalty score
- `Task.advertised_reward_cents` — tracks listed reward at accept time
- `EventType.PAYMENT_DISPUTE` — new event type (exists but not triggered)
- `LedgerCategory.PAYMENT_DISPUTE` — new ledger category

## Config: 3 New Knobs

| Knob | Medium Value | Meaning |
|------|-------------|---------|
| `loyalty_rat_fraction` | 0.35 | ~35% of clients are RATs (~2 out of 6) |
| `loyalty_severity` | 2.0 | Scope creep = severity × \|loyalty\| (up to +130% work) |
| `loyalty_reveal_trust` | 0.0 | Effects active from first task (no trust threshold) |

Derived: `scope_creep_max = severity × 1.0`, `dispute_clawback_max = severity × 1.2` (unused), `dispute_prob_max = severity × 1.0` (unused).

## Medium Preset

Redesigned to test client trust awareness:

| Parameter | Value | Why |
|-----------|-------|-----|
| `num_employees` | 8 | Lower payroll, still enough for fast completion |
| `initial_funds_cents` | $200,000 | Comfortable runway |
| `num_clients` | 6 | Fewer clients = more tasks per client = loyalty effects visible |
| `deadline_min_biz_days` | 6 | Normal tasks OK at 1-2 concurrent. RATs fail at 1 concurrent |
| `deadline_qty_per_day` | 150 | Moderate deadlines |
| `salary_bump_pct` | 0.0 | Flat payroll — no compounding weirdness |
| `task_progress_milestones` | [] | No 25/50/75% interrupts — resume goes straight to completions |
| `trust_build_rate` | 10 | Fast trust build so loyalty effects matter in 1 year |
| `trust_gating_fraction` | 0.15 | 15% of high-reward tasks require trust |
| Domain count | 1 (constant) | Single-domain — focus is on client choice, not assignment |
| Prestige dist | mode=1 | Most tasks accessible immediately |

## CLI Changes

| Command | Change |
|---------|--------|
| `task assign-all` | New — assigns all employees in one call |
| `market browse` | Auto-filters by company's max prestige. Trust-gated tasks still shown (model skill test) |
| `client history` | Shows `tasks_succeeded`, `tasks_failed`, `failure_rate_pct` per client |
| `sim resume` | Wake events include `task_title` and `client_name` on completions |

## Agent Prompt Changes

- Initial instructions use `assign-all` instead of individual assigns
- "Check each command's result. If accept fails, try a different task"
- "Do NOT call sim resume without an active task"
- "You can batch multiple tasks before calling sim resume"
- Economy section: prestige scaling, per-domain skills, cross-client trust decay
- Removed `--required-prestige-lte` from docs (auto-filtered now)
- Removed prescriptive strategy advice

## Task Generation Changes

- Client `reward_multiplier` applied to task rewards
- RATs get normal multipliers (no bait — RATs look identical)

## Dashboard

- **World tab**: Employees with skill bars, clients with RAT/LOYAL badges, active tasks with progress
- **Funds chart**: Overlays all model runs from same directory
- **Live transcript**: `.transcript.jsonl` written during runs
- Auto-refreshes every 5 seconds

## Findings So Far

| Model | Funds | OK | Fail | RAT fail |
|-------|-------|----|------|----------|
| GPT-5.2 | $663K | 91 | 16 | 3 |
| Gemini Pro | $351K | 67 | 5 | 1 |
| Greedy Bot | $201K | 55 | 8 | 6 |
| Gemini Flash | $65K | 33 | 7 | 1 |

Key behavioral differences:
- **GPT-5.2**: Recovers from failed accepts by trying other tasks. Builds deep client trust (work reduction virtuous cycle). High throughput.
- **Gemini Pro**: Decent throughput, avoids RATs, but has month-long idle gaps from failed trust-gated accepts.
- **Greedy Bot**: Can't detect RATs. Picks highest reward blindly. 6 RAT failures.
- **Gemini Flash**: Calls `sim resume` after failed accepts, wasting entire months. Model can't reason about command failure recovery.
