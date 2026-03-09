# Runner & Orchestration

**Location**: `src/yc_bench/runner/`

## Overview

The runner is the top-level orchestration layer that ties everything together: parsing arguments, loading configuration, initializing the database, seeding the world, starting the agent loop, and collecting results.

## Components

### Entry Point (`main.py`)

```python
def run_benchmark(args):
    # 1. Load configuration
    cfg = load_config(args.config)

    # 2. Initialize database
    engine, factory = init_db(db_path)

    # 3. Seed world
    with session_scope(factory) as session:
        seed_world_transactional(session, cfg, args.seed)

    # 4. Build agent runtime
    runtime = build_runtime(cfg.agent, args.model)

    # 5. Start dashboard (if TTY)
    dashboard = Dashboard(cfg) if is_tty() else None

    # 6. Run agent loop
    result = run_agent_loop(runtime, factory, cfg, dashboard)

    # 7. Save results
    save_rollout(result, args.output)
```

### Design Choices

#### Single-Command Invocation

```bash
uv run yc-bench run --model gemini/gemini-3-flash --seed 1 --config medium
```

**Why single command?** Benchmarks should be easy to reproduce. One command with explicit parameters (model, seed, config) fully specifies a run.

#### Database Per Run

Each run creates a fresh SQLite database:

```
db/run_seed1_medium_2025-03-15.sqlite
```

**Why per-run databases?**
- Isolation: runs can't interfere with each other
- Inspection: can analyze any run's final state after the fact
- Reproducibility: re-running with same seed produces identical database
- Parallelism: multiple runs can execute simultaneously

## Argument Parsing (`args.py`)

### Key Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `--model` | Yes | LLM model identifier (LiteLLM format) |
| `--seed` | Yes | Random seed for world generation |
| `--config` | No | Difficulty preset (default: "medium") |
| `--output` | No | Output path for rollout JSON |
| `--no-dashboard` | No | Disable live terminal UI |
| `--max-turns` | No | Override turn limit |

**Design choice**: Required arguments are minimal (model + seed). Everything else has sensible defaults. This reduces barrier to running benchmarks while allowing full customization.

## Dashboard (`dashboard.py`)

### Live Terminal UI

The dashboard uses [Rich](https://github.com/Textualize/rich) to display real-time simulation state:

```
┌─ YC-Bench Dashboard ──────────────────────────────┐
│ Model: claude-sonnet-4  Seed: 42  Config: medium  │
│ Turn: 87/500  Sim Time: 2025-06-15                 │
├────────────────────────────────────────────────────┤
│ Funds: $125,340  Runway: 4.2 months                │
│ Prestige: R:5.2  I:3.8  D:2.1  T:6.4              │
│ Active Tasks: 3  Completed: 12  Failed: 1          │
├────────────────────────────────────────────────────┤
│ Last Action: task assign abc123 emp456              │
│ Last Event: task_completed (success)               │
└────────────────────────────────────────────────────┘
```

**Design choice**: The dashboard is for human observers, not the agent. It provides real-time visibility into benchmark runs without affecting agent behavior.

### Features

- Live fund tracking with trend indicators
- Prestige levels per domain
- Task status counters
- Recent agent actions
- Turn counter and simulation clock
- Auto-refreshes on each turn

### Conditional Activation

Dashboard only activates when running in a TTY (interactive terminal). Redirected output or CI environments get plain log output.

**Why conditional?** Batch runs (scripts/) shouldn't have terminal UI overhead. Detecting TTY ensures the right output mode automatically.

## Session Management (`session.py`)

### Run Session

Manages the lifecycle of a single benchmark run:

```python
class RunSession:
    db_path: str
    config: ExperimentConfig
    model: str
    seed: int
    start_time: datetime

    def save_rollout(self, result):
        """Save final rollout JSON to results/"""

    def cleanup(self):
        """Clean up temporary resources"""
```

**Design choice**: Session object encapsulates all run-specific state, making it easy to serialize and manage runs.

## Batch Running (`scripts/`)

### Multi-Seed Runs

Scripts for running the same model across multiple seeds:

```bash
# Run seeds 1-10 with claude-sonnet on medium difficulty
for seed in $(seq 1 10); do
    uv run yc-bench run --model anthropic/claude-sonnet-4-20250514 --seed $seed --config medium
done
```

### Multi-Model Comparison

Scripts for comparing models on the same seeds:

```bash
for model in "anthropic/claude-sonnet-4-20250514" "openai/gpt-4o" "google/gemini-pro"; do
    uv run yc-bench run --model $model --seed 42 --config medium
done
```

**Design choice**: Simple shell scripts rather than a complex orchestration framework. This keeps the benchmark tooling minimal and transparent.

## Results & Output

### Rollout JSON

Each run produces a rollout file:

```
results/
├── claude-sonnet_seed1_medium.json
├── claude-sonnet_seed2_medium.json
├── gpt-4o_seed1_medium.json
└── ...
```

### Rollout Contents

```json
{
    "metadata": {
        "model": "anthropic/claude-sonnet-4-20250514",
        "seed": 1,
        "config": "medium",
        "start_time": "2025-03-15T10:00:00",
        "end_time": "2025-03-15T10:45:00"
    },
    "outcome": "horizon_end",
    "final_state": {
        "funds_cents": 25000000,
        "prestige": {"research": 7.2, "inference": 5.1, ...},
        "tasks_completed": 24,
        "tasks_failed": 3,
        "tasks_cancelled": 1,
        "turns_used": 187
    },
    "transcript": [
        {"turn": 1, "action": "company status", "result": {...}},
        ...
    ]
}
```

### Plots (`plots/`)

Visualization scripts for comparing model performance:
- Funds over time
- Prestige progression per domain
- Task completion rates
- Comparison charts across models/seeds

**Design choice**: Separate plotting from the benchmark runner. Results are stored as data (JSON); visualization is a post-processing step.

## Error Recovery

### Crash Recovery

If a run crashes (LLM timeout, OOM, etc.):
- The SQLite database persists with the last consistent state
- Rollout JSON may be partial but includes transcript up to the crash
- Re-running with the same seed starts fresh (no resume from crash)

**Design choice**: No crash recovery by design. Benchmark runs should be atomic -- either complete or re-run. This prevents partial results from contaminating comparisons.

### Graceful Shutdown

On SIGINT (Ctrl+C):
- Current turn completes
- Partial rollout is saved
- Database is committed
- Dashboard is cleaned up

**Design choice**: Graceful shutdown preserves whatever data exists, useful for debugging long runs that need to be interrupted.
