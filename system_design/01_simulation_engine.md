# Simulation Engine

**Location**: `src/yc_bench/core/`

## Design Choice: Discrete-Event Simulation

YC-Bench uses a **discrete-event simulation (DES)** model rather than a tick-based approach. This was chosen because:

1. **Determinism**: Events are processed in a fixed, reproducible order given the same seed
2. **Efficiency**: Time jumps between events rather than iterating every hour/day
3. **Clarity**: Each state change corresponds to a meaningful event, making the simulation auditable

## Core Loop (`engine.py`)

The `advance_time()` function is the heart of the simulation:

```
advance_time(session, company_id, cfg) → AdvanceResult
```

### Algorithm

1. **Flush progress** on all active tasks (convert elapsed business hours into completed work)
2. **Apply prestige decay** for elapsed days
3. **Process payroll** if crossing a month boundary (first business day)
4. **Fetch next unconsumed event** ordered by `(scheduled_at, priority)`
5. **Dispatch to handler** based on event type
6. **Recalculate ETAs** for affected tasks
7. **Update sim_time** to the event's timestamp
8. **Return wake events** to the agent

### Why "Resume" Rather Than Auto-Advance?

The agent explicitly calls `yc-bench sim resume` to advance time. This design:

- Gives the agent control over pacing (plan before advancing)
- Creates a natural decision checkpoint between simulation steps
- Allows multiple CLI queries before committing to advancing
- If the agent stalls (N turns without resume), the loop forces one automatically

## Event System (`events.py`)

### Event Types (Priority Order)

| Priority | Event Type | Trigger |
|----------|-----------|---------|
| 1 | `task_completed` | Task reaches 100% in all domain requirements |
| 2 | `bankruptcy` | Funds drop below zero after payroll |
| 3 | `task_half` | Task reaches 50% progress milestone |
| 4 | `horizon_end` | Simulation time limit reached |

### Design Choice: Fixed Priority Ordering

Events at the same timestamp are processed in strict priority order. This ensures:

- Task completions (and their rewards) are processed before bankruptcy checks
- A task finishing on the same day as payroll can save the company from bankruptcy
- Deterministic behavior regardless of insertion order

### Event Identity (Deterministic UUIDs)

Event IDs use `uuid5` based on payload + timestamp + dedupe_key. This means:

- Same world state produces identical event IDs
- Deduplication is automatic (re-inserting same event is a no-op)
- Full reproducibility across runs with same seed

## Event Handlers (`handlers/`)

### `task_complete.py`
- Finalizes all domain progress to 100%
- Success check: `sim_time <= deadline`
- On success: add reward funds, add prestige per domain, boost employee skill rates, apply 1% salary bump
- On failure (late): apply prestige penalty per domain (configurable multiplier)

### `task_half.py`
- Marks progress milestone reached
- Informational event for agent awareness (no state changes beyond flag)

### `bankruptcy.py`
- Triggered when `funds_cents < 0` after payroll
- Terminates the simulation with bankruptcy outcome

### `horizon_end.py`
- Triggered at configured simulation end date
- Terminates the simulation with final scoring

## Progress Tracking (`progress.py`)

### Effective Rate Calculation

```
effective_rate = base_rate_per_hour / num_active_tasks_for_this_employee
```

**Design choice**: Throughput splitting creates a resource allocation puzzle. An employee assigned to 3 tasks works at 1/3 speed on each. The agent must balance parallelism vs. focus.

### Progress Flush

When `advance_time()` runs, it calculates work done since the last flush:

```
work = effective_rate × business_hours_elapsed
completed_qty += work  (capped at required_qty)
```

## Business Time (`business_time.py`)

### Design Choice: Business Hours Only

Work only happens during business hours (weekdays, configurable hours per day). This adds:

- Realistic scheduling constraints
- Weekend gaps that affect deadline calculations
- A reason for the agent to think about calendar timing

## ETA Solver (`eta.py`)

### Completion Time

```
solve_task_completion_time():
  For each domain d:
    remaining[d] = required_qty[d] - completed_qty[d]
    rate[d] = sum(effective_rate for assigned employees with skill in d)
    time[d] = remaining[d] / rate[d]
  completion_time = max(time[d]) across all domains
```

### Design Choice: Multi-Domain Bottleneck

A task completes when ALL domains finish. The slowest domain determines completion time. This creates interesting assignment puzzles where the agent must identify and address bottlenecks.

### Halfway Time

Used for progress milestone events. Calculated as the weighted midpoint across domains.

## Prestige Decay

```
apply_prestige_decay(session, company_id, days_elapsed, cfg):
  for each domain:
    prestige -= decay_per_day × days_elapsed
    prestige = max(prestige, prestige_min)  # floor at 1.0
```

**Design choice**: Decay prevents "set and forget" strategies. The agent must continuously work in domains to maintain access to high-tier tasks. Neglected domains revert to baseline.
