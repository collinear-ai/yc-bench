# <img src="imgs/yc_bench.png" alt="YC-Bench logo" width="40" /> YC-Bench

A long-horizon deterministic benchmark for LLM agents. The agent plays CEO of an AI startup over a simulated 1–3 year run, operating exclusively through a CLI tool against a SQLite-backed discrete-event simulation.

The benchmark tests whether agents can manage compounding decisions: prestige specialisation, employee allocation, cash flow, and deadline risk — sustained over hundreds of turns.

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

### API key

```bash
# .env  (any LiteLLM-compatible provider)
ANTHROPIC_API_KEY="sk-ant-..."     # for anthropic/claude-*
GEMINI_API_KEY="AIza..."           # for gemini/gemini-*
OPENROUTER_API_KEY="sk-or-v1-..."  # for openrouter/*
OPENAI_API_KEY="sk-..."            # for openai/*
```

### Run

```bash
uv run yc-bench run \
  --model gemini/gemini-3-flash-preview \
  --seed 1 \
  --config medium
```

Outputs a SQLite DB in `db/` and a JSON rollout in `results/`.

### Run multiple models in parallel

```bash
bash scripts/run_benchmark.sh --seed 1 --config hard
```

---

## How it works

![YC Bench Architecture](imgs/arch.png "Architecture YC-Bench")

### Core loop

1. Agent calls `yc-bench sim resume` to advance time to the next event or monthly payroll.
2. The engine flushes task progress, applies prestige decay, fires due events, applies payroll.
3. Agent reads wake events and decides: accept tasks, assign employees, dispatch, cancel.
4. Repeat until bankruptcy or horizon end.

The simulation ends on **bankruptcy** (funds < 0 after payroll), **horizon end** (1–3 years), or **max turns** (if configured). If the agent doesn't call `sim resume` for 10 consecutive turns, the loop forces one automatically.

### Key mechanics

- **Funds**: starting capital varies by preset ($80K–$250K). Monthly payroll is deducted automatically. Task rewards scale with prestige (`base × (1 + scale × (prestige − 1))`).
- **4 domains**: `research · inference · data/environment · training`. Each domain tracks prestige independently in [1.0, 10.0].
- **Per-domain prestige gating**: a task's required prestige is checked against **each** of its required domains. The agent must climb prestige broadly, not just in one domain.
- **Prestige decay**: every domain loses prestige daily. Neglected domains decay back toward 1.0. The agent must stay active across domains to maintain market access.
- **Prestige-scaled work volume**: higher-prestige tasks require proportionally more work. Higher prestige pays more but demands more capacity.
- **Employees**: 10 employees across 3 tiers (junior/mid/senior). The agent sees only each employee's tier and salary — not their per-domain skill rates. A junior can secretly be a superstar in one domain, so the agent must infer productivity from task progress observations.
- **Throughput splitting**: an employee assigned to N active tasks has `effective_rate = base_rate / N`. Focus beats breadth.
- **Task success**: on-time completion awards funds + prestige + skill boosts + 1% salary bump (compounding payroll pressure). Late completion penalises prestige. Cancellation penalises harder.
- **Progress checkpoints**: the agent is woken at 25%, 50%, 75%, and 100% completion — providing data points to estimate employee productivity.
- **Scratchpad**: persistent notes in the DB that survive context truncation (only last 20 conversation rounds are kept).

### Agent CLI

All commands return JSON. The agent interacts via `run_command("yc-bench <cmd>")`.

```bash
# Observe
yc-bench company status                          # funds, prestige, runway
yc-bench employee list                           # tier, salary, active tasks
yc-bench market browse [--domain X] [--limit N]  # available tasks
yc-bench task list [--status X]                  # your tasks
yc-bench task inspect --task-id UUID             # progress, deadline, assignments
yc-bench finance ledger                          # transaction history
yc-bench report monthly                          # P&L per month

# Act
yc-bench task accept --task-id UUID              # pull from market
yc-bench task assign --task-id UUID --employee-id UUID
yc-bench task dispatch --task-id UUID            # start work
yc-bench task cancel --task-id UUID --reason ""  # cancel (prestige penalty)
yc-bench sim resume                              # advance time
yc-bench scratchpad write/append/clear           # persistent memory
```

---

## Configuration

Experiment presets live in `src/yc_bench/config/presets/` as TOML files. Pass the preset name via `--config`.

All presets use 10 employees and 200 market tasks. Difficulty comes from deadline pressure, penalty severity, prestige distribution, and task size.

| Config | Deadline pressure | Prestige mode | What it tests |
|--------|------------------|---------------|---------------|
| **tutorial** | Very relaxed | 1 | Basic accept→assign→dispatch loop |
| **easy** | Relaxed | 1 | Throughput awareness |
| **medium** | Moderate | 3 | Prestige climbing + domain specialization |
| **hard** | Tight | 4 | Precise ETA reasoning + capacity planning |
| **nightmare** | Razor-thin | 5 | Sustained perfection under compounding payroll |

See `default.toml` for the full list of tunable parameters.

---

## Benchmark results

*Results pending — re-running benchmarks with updated economics.*

---

Please cite our work if you find it useful!

```bibtex
@misc{collinear-ai2025ycbench,
  author       = {{Collinear AI}},
  title        = {{YC-Bench}: Your Company Bench — A Long-Horizon Coherence Benchmark for {LLM} Agents},
  year         = {2025},
  howpublished = {\url{https://github.com/collinear-ai/yc-bench}},
  note         = {Accessed: 2026-02-25}
}
```
