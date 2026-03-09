# YC-Bench: System Overview

## What is YC-Bench?

YC-Bench is a **long-horizon deterministic benchmark for LLM agents**. It simulates an AI startup CEO managing a company over 1-3 years through a CLI-based interface against a SQLite-backed discrete-event simulation engine. The benchmark tests sustained decision-making over hundreds of turns through compounding financial, prestige, and deadline pressures.

## Core Premise

An LLM agent is dropped into the role of CEO of a small AI startup. It must:

- Browse and accept tasks from a marketplace
- Assign employees to tasks across 4 technical domains
- Manage cash flow (payroll, rewards, penalties)
- Build prestige in each domain to unlock higher-tier tasks
- Survive until the simulation horizon ends without going bankrupt

## Key Metrics (~4,975 lines of Python)

| Dimension | Details |
|-----------|---------|
| Employees | 10 (hidden per-domain skill rates) |
| Market Tasks | 200+ (configurable) |
| Domains | 4: research, inference, data_environment, training |
| Prestige Range | 1.0 - 10.0 per domain |
| Difficulty Presets | tutorial, easy, medium, hard, nightmare |

## High-Level Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Runner / CLI                       │
│  (argument parsing, dashboard, session management)   │
├─────────────────────────────────────────────────────┤
│                   Agent Layer                        │
│  (LLM runtime, agent loop, tools, prompt building)   │
├─────────────────────────────────────────────────────┤
│              CLI Command Interface                   │
│  (company, employee, market, task, sim, finance,     │
│   report, scratchpad)                                │
├─────────────────────────────────────────────────────┤
│              Simulation Engine (core/)               │
│  (event processing, ETA solving, progress tracking,  │
│   business time, prestige decay)                     │
├─────────────────────────────────────────────────────┤
│              Data Layer (db/)                        │
│  (SQLAlchemy ORM models, session management)         │
├─────────────────────────────────────────────────────┤
│         Configuration & World Generation             │
│  (Pydantic schemas, TOML presets, seeding, RNG)      │
└─────────────────────────────────────────────────────┘
```

## Directory Map

```
~/yc_bench_fixed/
├── src/yc_bench/
│   ├── __main__.py              # CLI entry point
│   ├── agent/                   # Agent runtime and loop
│   ├── cli/                     # Agent-facing CLI commands
│   ├── core/                    # Simulation engine
│   ├── db/                      # ORM models & session
│   ├── config/                  # Pydantic schemas + TOML presets
│   ├── services/                # World generation & RNG
│   └── runner/                  # Benchmark orchestration
├── scripts/                     # Batch running scripts
├── db/                          # SQLite databases (runtime)
├── results/                     # Output JSON rollouts
├── plots/                       # Result visualizations
├── pyproject.toml               # Package definition (uv-based)
└── uv.lock                     # Lock file
```

## Execution Flow

1. User runs: `uv run yc-bench run --model <model> --seed 1 --config medium`
2. Runner loads config, initializes DB, seeds world, starts agent loop
3. Agent receives system prompt with company context and available CLI tools
4. Each turn: agent calls CLI commands via `run_command` tool, optionally `python_repl`
5. Agent calls `yc-bench sim resume` to advance simulation time
6. Simulation processes events (completions, payroll, milestones) and returns wake events
7. Loop continues until bankruptcy or horizon end
8. Output: rollout JSON transcript + SQLite game state

## Design Documents

| File | Topic |
|------|-------|
| [01_simulation_engine.md](01_simulation_engine.md) | Core simulation engine and event processing |
| [02_data_models.md](02_data_models.md) | Database schema and ORM design |
| [03_task_system.md](03_task_system.md) | Task lifecycle, ETA, and progress |
| [04_prestige_system.md](04_prestige_system.md) | Prestige mechanics, decay, and gating |
| [05_financial_model.md](05_financial_model.md) | Funds, payroll, ledger, and bankruptcy |
| [06_employee_model.md](06_employee_model.md) | Employee skills, throughput, and growth |
| [07_agent_layer.md](07_agent_layer.md) | LLM runtime, agent loop, and tools |
| [08_cli_interface.md](08_cli_interface.md) | CLI command groups and JSON output |
| [09_configuration.md](09_configuration.md) | Config schema, presets, and world generation |
| [10_runner_orchestration.md](10_runner_orchestration.md) | Benchmark runner, dashboard, and session |
