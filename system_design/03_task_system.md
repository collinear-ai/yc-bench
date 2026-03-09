# Task System

**Location**: `src/yc_bench/cli/task_commands.py`, `src/yc_bench/core/eta.py`, `src/yc_bench/core/progress.py`

## Task Lifecycle

```
market ──accept──> planned ──dispatch──> active ──complete──> completed_success
                      │                    │                  completed_fail
                      │                    │
                      └──cancel──> cancelled <──cancel──┘
```

### States

| Status | Meaning |
|--------|---------|
| `market` | Available for browsing, not yet accepted |
| `planned` | Accepted by company, employees can be assigned |
| `active` | Dispatched, work is progressing |
| `completed_success` | Finished on time |
| `completed_fail` | Finished late (past deadline) |
| `cancelled` | Abandoned by agent |

## Design Choices

### Two-Phase Activation (Accept → Dispatch)

Tasks go through `planned` before `active`. This separation:

1. **Allows pre-assignment**: Agent can assign employees before starting the clock
2. **Deadline starts at accept**: Creates urgency -- planning time counts against the deadline
3. **Forces commitment**: Accepting a task reserves it but the agent must still dispatch

### Deadline Calculation

```
deadline = accepted_at + max(required_qty[d] for all domains d) / deadline_qty_per_day
```

**Design choice**: Deadline is proportional to the largest single-domain requirement, not the sum. This means multi-domain tasks don't get proportionally more time -- they require parallel work.

### Prestige Gating at Accept Time

```python
def task_accept(task_id):
    for domain in task.requirements:
        if company_prestige[domain] < task.required_prestige:
            reject("Insufficient prestige in {domain}")
```

**Design choice**: Prestige check is per-domain. A task requiring prestige 3.0 with requirements in `research` and `inference` needs prestige >= 3.0 in BOTH domains. This prevents gaming by maxing one domain.

### Cancel Penalties

Cancelling an active task incurs:
- Prestige penalty: `reward_prestige_delta × cancel_multiplier` (configurable per difficulty)
- No financial penalty (just lost opportunity)

**Design choice**: Cancel penalties prevent the strategy of accepting everything and dropping what's inconvenient. Higher difficulties increase the cancel multiplier.

## Employee Assignment

### Assignment Rules

- Employees can only be assigned to `planned` or `active` tasks
- An employee can work on multiple tasks simultaneously (throughput splits)
- Multiple employees can work on the same task (parallel progress)

### Throughput Splitting

```
effective_rate = base_rate_per_hour / num_active_tasks
```

**Design choice**: Linear throughput splitting creates a fundamental trade-off:
- **Focus**: 1 employee on 1 task = full speed
- **Parallel**: 1 employee on 3 tasks = 1/3 speed each
- The agent must decide between fast completion of few tasks vs. slow progress on many

## Progress Tracking (`progress.py`)

### How Work Gets Done

Progress is calculated lazily during `advance_time()`:

```python
for each active task:
    for each assigned employee:
        for each domain in task requirements:
            work = employee.skill_rate[domain] / num_active_tasks × business_hours
            requirement.completed_qty += work
            requirement.completed_qty = min(completed_qty, required_qty)
```

### Multi-Domain Completion

A task is complete when ALL domain requirements reach `completed_qty >= required_qty`. The slowest domain is the bottleneck.

**Design choice**: This creates interesting optimization puzzles. If a task needs 100 units of research and 50 units of training, the agent should allocate more research-skilled employees to balance completion times.

## ETA Solver (`eta.py`)

### Completion Time Calculation

```python
def solve_task_completion_time(task, assignments, sim_time):
    for each domain d:
        remaining = required_qty[d] - completed_qty[d]
        rate = sum(effective_rate[emp][d] for emp in assignments)
        if rate == 0:
            return infinity  # no one can work on this domain
        hours_needed[d] = remaining / rate

    max_hours = max(hours_needed.values())
    return sim_time + max_hours (in business hours)
```

### Halfway Time Calculation

Used for milestone events. Finds the time when weighted average across domains reaches 50%.

### When ETAs Are Recalculated

- Task dispatched (new active task)
- Employee assigned/unassigned
- Task completed (frees employee throughput for other tasks)
- Task cancelled (same)

**Design choice**: Dynamic ETA recalculation ensures events are always accurate. When an employee is reassigned, all affected tasks get new completion projections.

## Market Task Generation

See [09_configuration.md](09_configuration.md) for details on how market tasks are generated with stratified prestige distribution and randomized requirements.

### Browsing and Filtering

The `market browse` command supports:
- Domain filter
- Prestige range filter
- Reward range filter
- Pagination (offset/limit)

All output is JSON for agent consumption.
