# YC-Bench

A long-horizon deterministic benchmark for LLM agents. The agent plays CEO of an AI startup over a simulated 1–3 year run, operating exclusively through a CLI tool against a SQLite-backed discrete-event simulation.

The benchmark tests whether agents can manage compounding decisions: prestige specialisation, employee allocation, cash flow, and deadline risk — sustained over hundreds of turns.

---

## Simulation Dynamics

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          AGENT (LLM)                                    │
│                                                                         │
│  Observes: company status · employee skills · market tasks · ledger     │
│  Acts via: run_command("yc-bench <cmd>")  ·  scratchpad (persistent)    │
└───────────────────────┬─────────────────────────────────────────────────┘
                        │ CLI commands (JSON responses)
                        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     DISCRETE-EVENT SIMULATION                           │
│                                                                         │
│  ┌─────────────┐    accept     ┌──────────┐   assign+dispatch           │
│  │   MARKET    │ ──────────►  │  PLANNED │ ──────────────────►          │
│  │  100 tasks  │              └──────────┘                              │
│  └─────────────┘                                                        │
│        ▲ replenish                     ┌──────────────────────┐         │
│        │                               │       ACTIVE         │         │
│        │   ┌────────────────────────── │  progress flushes    │         │
│        │   │                           │  every sim-advance   │         │
│        │   │                           └──────────┬───────────┘         │
│        │   │  ┌────────────────────────────────────┘                    │
│        │   │  │  ETA solver fires TASK_COMPLETED event                  │
│        │   │  ▼                                                         │
│        │   │  ┌────────────────────────────────────────────────────┐    │
│        │   │  │            TASK_COMPLETED handler                  │    │
│        │   │  │                                                    │    │
│        │   │  │  on_time?  YES → +reward_funds  +prestige_delta    │    │
│        │   │  │                  +skill_boost   +salary_bump       │    │
│        │   │  │            NO  → -1.4× prestige_delta (penalty)    │    │
│        └───┘  └─────────────────────┬───────────────────────────── ┘    │
│                                     │                                   │
│  ┌──────────────────────────────────┘                                   │
│  │  Monthly payroll (1st biz day)    Bankruptcy check (funds < 0)       │
│  │  Horizon end (1–3 years)          Context truncation (last 20 rounds)│
└──┴──────────────────────────────────────────────────────────────────────┘
```

### Core loop

1. Agent calls `yc-bench sim resume` to advance time to the next event.
2. The engine flushes task progress, fires due events, applies payroll.
3. Agent reads wake events and decides: accept tasks, assign employees, dispatch, cancel.
4. Repeat until bankruptcy or horizon end.

If the agent doesn't call `sim resume` for N consecutive turns (default 10), the loop forces one automatically.

---

## Economy

### Funds

- Start: **$250,000** (`initial_funds_cents = 25_000_000`)
- Payroll deducted on the **first business day of each month**
- Task reward formula: `base × (1 + reward_prestige_scale × (prestige_req − 1))`
  - Base: triangular sample in [$5K, $100K], mode $30K
  - `reward_prestige_scale = 0.55` (default): a prestige-8 task pays ~4.85× more than prestige-1

### Monthly payroll (5 employees, fast_test)

| Tier | Share | Salary/month | Skill rate |
|------|-------|-------------|------------|
| Junior | 50% | $2K–$4K | 1.0–6.5 units/hr |
| Mid | 35% | $6K–$8K | 3.5–8.5 units/hr |
| Senior | 15% | $10K–$15K | 5.5–10.0 units/hr |

Monthly payroll ≈ **$32K** (5 employees). Starting runway ≈ **7.8 months**.

### Task completion rewards

On success:
- Funds += `reward_funds_cents`
- Prestige += `reward_prestige_delta` (beta-distributed, typically 0.1–1.5) per required domain
- Skill rate += `skill_boost_pct × current_rate` per assigned employee per domain
- Salary += `1% × current_salary` per assigned employee (compounding payroll pressure)

On failure (past deadline):
- Prestige −= `1.4 × reward_prestige_delta` per domain

On cancel:
- Prestige −= `2.0 × reward_prestige_delta` per domain

---

## Prestige

7 domains: `system · research · data · frontend · backend · training · hardware`

- Range: **[1.0, 10.0]** per domain, starts at 1.0
- Tasks require a minimum prestige level. Agent can only accept tasks where `max(company_prestige) >= required_prestige`.
- Default distribution: mode=4, so most tasks need prestige 3–5.
- First 10 market tasks are stratified `[1,1,1,1,2,2,2,3,3,4]` to bootstrap progression.

Specialising in 2–3 domains unlocks progressively higher-reward tasks. Spreading thin keeps you locked at low prestige everywhere.

---

## Employee throughput

Each employee has a skill rate (units/hr) per domain.

When an employee is assigned to N active tasks simultaneously:

```
effective_rate_per_task = base_rate / N
```

Assigning one senior (rate 8.0) to 4 tasks gives 2.0 units/hr each — often worse than a junior focused on one.

Task completion time = `max(remaining[d] / effective_rate[d])` across all required domains.

Deadline = `max(7, total_required_qty / deadline_qty_per_day)` business days.

`deadline_qty_per_day = 200` in both `challenge` and `fast_test`. With 10 employees and 5 focused per domain, team throughput ≈ 230 units/domain/day — achievable for up to ~4 simultaneous tasks.

---

## Agent interface

All commands return JSON to stdout.

### Observe
```bash
yc-bench company status              # funds, prestige, runway, payroll
yc-bench employee list               # skills, salary, active tasks
yc-bench market browse               # available tasks (--limit N --offset N)
yc-bench task list [--status X]      # planned|active|completed_*|cancelled
yc-bench task inspect --task-id UUID # progress %, deadline, assignments
yc-bench finance ledger              # full transaction history
yc-bench report monthly              # P&L per month
yc-bench scratchpad read             # persistent notes (survives context truncation)
```

### Act
```bash
yc-bench task accept --task-id UUID             # pull from market, set deadline
yc-bench task assign --task-id UUID --employee-id UUID
yc-bench task dispatch --task-id UUID           # start work (≥1 assignment required)
yc-bench task cancel --task-id UUID --reason "" # 2× prestige penalty
yc-bench sim resume                             # advance to next event
yc-bench scratchpad write/append/clear          # persistent memory
```

---

## Context management

- **Proactive truncation**: keeps the last 20 conversation rounds before each API call. Older rounds are dropped.
- **Scratchpad**: per-company persistent text in DB. Survives truncation. Use it to store strategy, deadlines, and employee assignments.

---

## Repository layout

```
YC_Bench/
├── src/              # Python package (yc_bench)
├── scripts/          # plot_multi_model.py, run_benchmark.sh
├── logs/             # per-model stdout/stderr logs
├── db/               # SQLite databases (one per model run)
├── results/          # JSON rollout files
├── plots/            # generated PNG charts
├── pyproject.toml
└── README.md
```

---

## Setup

### Prerequisites

- Python 3.12+
- [`uv`](https://github.com/astral-sh/uv)

### Install

```bash
git clone <repo-url>
cd YC_Bench
uv sync
```

No database setup required — the runner auto-creates `db/<config>_<seed>_<model>.db` on first run.

### API key

```bash
# .env  (any LiteLLM-compatible provider)
ANTHROPIC_API_KEY="sk-ant-..."     # for anthropic/claude-*
GEMINI_API_KEY="AIza..."           # for gemini/gemini-*
OPENROUTER_API_KEY="sk-or-v1-..."  # for openrouter/*
OPENAI_API_KEY="sk-..."            # for openai/*
```

### Run a single model

```bash
uv run yc-bench run \
  --model gemini/gemini-3-flash-preview \
  --seed 1 \
  --config medium
```

Outputs:
- `db/medium_1_gemini_gemini-3-flash-preview.db` — SQLite simulation state
- `results/yc_bench_result_medium_1_gemini_gemini-3-flash-preview.json` — full rollout + transcript

### Run 5 models in parallel

```bash
bash scripts/run_benchmark.sh --seed 1 --config challenge
```

### Generate the comparison plot

```bash
uv run python scripts/plot_multi_model.py --seed 1 --config challenge --budget 30
# → plots/funds_curves.png
```

---

## Configuration

Experiment presets live in `src/yc_bench/config/presets/` as TOML files. Pass the preset name via `--config`.

```
src/yc_bench/config/presets/
├── default.toml      # 3yr, 10 employees, 500 tasks — base config
├── tutorial.toml     # 1yr,  3 employees,  50 tasks — learn the loop
├── easy.toml         # 1yr,  5 employees, 100 tasks — throughput awareness
├── medium.toml       # 1yr,  5 employees, 150 tasks — prestige strategy
├── hard.toml         # 1yr,  7 employees, 200 tasks — precise ETA reasoning
├── nightmare.toml    # 1yr,  8 employees, 300 tasks — sustained perfection
├── challenge.toml    # 3yr,  5 employees, 200 tasks — long-horizon endurance
└── fast_test.toml    # 1yr,  5 employees, 100 tasks — quick iteration
```

Each difficulty level tests one additional concept:

| Config | Tests | Key constraint |
|--------|-------|---------------|
| **tutorial** | Basic accept→assign→dispatch loop | All prestige-1, single domain |
| **easy** | Throughput awareness | Don't over-parallelize |
| **medium** | Prestige climbing + domain specialization | 2-domain tasks, prestige mode=3 |
| **hard** | Precise ETA computation | One bad accept degrades in-flight tasks |
| **nightmare** | Sustained perfection under compounding payroll | One failure ≈ fatal, salary bumps 2%/task |

### Key WorldConfig parameters

| Parameter | Default | Controls |
|-----------|---------|---------|
| `initial_funds_cents` | 25_000_000 | Starting cash ($250K) |
| `num_employees` | 5 | Workforce size |
| `num_market_tasks` | 100 | Market pool size |
| `required_prestige_mode` | 4 | Peak of prestige-req distribution |
| `domain_count_mode` | 2 | Most tasks require 2 domains |
| `required_qty_low/mode` | 500 / 1400 | Task work volume (units) |
| `deadline_qty_per_day` | 200 | Units completable per biz day (lower = easier) |
| `deadline_min_biz_days` | 7 | Minimum deadline |
| `penalty_fail_multiplier` | 1.4 | Prestige × this on deadline miss |
| `penalty_cancel_multiplier` | 2.0 | Prestige × this on cancel |
| `reward_prestige_scale` | 0.55 | Extra reward fraction per prestige level above 1 |
| `salary_bump_pct` | 0.01 | Salary raise per employee per completed task |

### AgentConfig

| Parameter | Default | Controls |
|-----------|---------|---------|
| `model` | openrouter/openai/gpt-4o-mini | LLM model string |
| `temperature` | 0.0 | Sampling temperature |
| `history_keep_rounds` | 20 | Conversation rounds kept in context |

### LoopConfig

| Parameter | Default | Controls |
|-----------|---------|---------|
| `auto_advance_after_turns` | 5 | Force sim resume after N turns without one |
| `max_turns` | 50 | Hard cap on agent turns (null = unlimited) |

### Environment overrides

```bash
YC_BENCH_EXPERIMENT=fast_test     # select preset
DATABASE_URL=sqlite:///custom.db  # SQLite path
```

---

## Terminal conditions

| Condition | Trigger |
|-----------|---------|
| Horizon end | `sim_time >= start_date + horizon_years` |
| Bankruptcy | `funds_cents < 0` after any payroll |
| Error | Agent runtime exception (API failure, exhausted retries) |
| Max turns | `turn_count >= max_turns` (if set) |

---

## What makes it hard

The hardened default is designed so that the obvious strategies fail:

- **Prestige-1 farming** is unprofitable. Most replacement tasks need prestige 3–5 and pay much more. Farming the bottom locks you out.
- **Single-specialist dominance** is gone. Most tasks need 2 domains. You must allocate across skill combinations.
- **Speculative accepting** is punished. Cancel penalty (2×) exceeds fail penalty (1.4×) so you can't accept everything and drop the losers.
- **Ignoring payroll** causes bankruptcy. ~$32K/month burns your $250K in 7.8 months — but task complexity means you must also pace your accepts.
- **Parallel dispatch** dilutes throughput. Splitting employees across too many tasks extends every deadline — focus beats breadth.
- **Salary bumps compound**. Every task completion raises assigned employee salaries 1%. Payroll creep accelerates over time.

---

## Benchmark results

### Sonnet 4.6 vs Gemini 3 Flash vs GPT-5.2 — 1-year horizon, 3 seeds per config

![3-model comparison](plots/sonnet_vs_gemini.png)

#### Survival rates (at end of year 1)

| Config | Sonnet 4.6 | Gemini 3 Flash | GPT-5.2 |
|--------|-----------|----------------|---------|
| **medium** | 3/3 survived | 3/3 survived | 3/3 survived |
| **hard** | 1/3 survived | 2/3 survived | 2/3 survived |
| **nightmare** | 1/3 survived | 3/3 survived | 2/3 survived |

#### Final funds at 1-year mark (bankrupt = funds < 0)

| Config | Seed | Sonnet 4.6 | Gemini 3 Flash | GPT-5.2 |
|--------|------|-----------|----------------|---------|
| medium | 1 | **$9.1M** | **$9.5M** | **$1.8M** |
| medium | 2 | **$6.1M** | **$11.0M** | **$321K** |
| medium | 3 | **$107K** | **$15.8M** | **$28K** |
| hard | 1 | bankrupt | bankrupt | bankrupt |
| hard | 2 | **$63K** | **$412K** | **$15.7M** |
| hard | 3 | bankrupt | **$21.9M** | **$43.5M** |
| nightmare | 1 | bankrupt | **$2.1M** | bankrupt |
| nightmare | 2 | **$10.1M** | **$214K** | **$2.2M** |
| nightmare | 3 | bankrupt | **$805K** | **$23.6M** |

**Overall: Gemini 8/9 · GPT-5.2 7/9 · Sonnet 5/9**

### Key findings

**Gemini leads on consistency (8/9).** Near-perfect win rates on medium (93–98%), and the only model to sweep all 3 nightmare seeds. Achieves this without using the scratchpad — purely reactive, high-frequency decision-making.

**GPT-5.2 excels at hard (2/3, matching Gemini) with the highest absolute returns.** Hard seed 3: $43.5M vs Gemini's $21.9M. Nightmare seed 3: $23.6M vs Gemini's $805K. When GPT-5.2 survives, it tends to outperform by a significant margin.

**Sonnet has the highest ceiling when it works but the lowest floor.** Nightmare seed 2: $10.1M (best nightmare result). But 4/9 bankruptcies — Sonnet fails harder than the others on adverse seeds.

**Hard is the differentiator config.** On easy configs all three survive. On hard/nightmare the strategies diverge sharply. Gemini plays safe and consistent; GPT-5.2 swings big; Sonnet is high-variance.

**Win rate predicts survival.** Every run with >58% task win rate survived. Every run with <40% went bankrupt. Below that threshold, prestige losses from failures outpace gains and lock the agent out of profitable tasks.

### Why models fail

The scratchpad evolution of Sonnet on hard seed 2 tells the full story:

![Sonnet hard seed 2 scratchpad evolution](plots/notepad_hard_2_claude-sonnet-4-6.gif)

Common failure patterns across all bankrupt runs:

1. **Over-parallelization.** Accepting 3–5 tasks at once, splitting employees across them. Effective rate per task drops below deadline requirements. Sonnet nightmare seed 3 ran 5 tasks simultaneously with 8 employees on turn 13.
2. **No prestige gating.** Accepting prestige-2 tasks when company prestige is 1.0. The task completes late, triggers a 1.4× prestige penalty, and the agent ends up worse than before.
3. **Late adaptation.** Sonnet correctly identifies problems in its scratchpad ("PRESTIGE CRISIS — MARKET LOCK") but only after payroll has consumed the runway. By turn 137 of hard seed 2, all tasks require prestige ≥ 2 but the company is stuck at 1.0 in 6 of 7 domains.
4. **Inconsistent ETA reasoning.** Sonnet's medium seed 2 has a 49% win rate — essentially a coin flip. It understands throughput math in its scratchpad but doesn't consistently apply it when selecting tasks.

### Sonnet-only results by config

![Sonnet results](plots/sonnet_results.png)

---

## Simulation rules

- **Business time**: weekdays only, 09:00–18:00. No leap years.
- **Money**: stored as integer cents (`BIGINT`). No floating point.
- **Payroll**: fired on the first business day of each month.
- **Event ordering**: deterministic — `(scheduled_at, priority, id)`.
- **Determinism**: all task generation and employee seeding is reproducible given `--seed`.
- **Prestige**: `NUMERIC(6,3)`, hard clamped to `[1.0, 10.0]`.
- **DB reuse**: if a simulation is terminal (bankrupt or horizon reached), re-running with the same DB wipes and reseeds cleanly.

---

## Output format

`results/yc_bench_result_<config>_<seed>_<model>.json`:

```json
{
  "session_id": "run-1-openrouter/openai/gpt-4o-mini",
  "model": "openrouter/openai/gpt-4o-mini",
  "seed": 1,
  "horizon_years": 1,
  "turns_completed": 46,
  "terminal": true,
  "terminal_reason": "bankruptcy",
  "total_cost_usd": 0.100008,
  "started_at": "...",
  "ended_at": "...",
  "transcript": [
    {
      "turn": 1,
      "timestamp": "...",
      "user_input": "## Simulation Start ...",
      "agent_output": "Executed 3 tool call(s): ...",
      "commands_executed": ["yc-bench company status -> {...}", ...]
    }
  ]
}
```
