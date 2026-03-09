# Data Models & Database Design

**Location**: `src/yc_bench/db/`

## Design Choice: SQLAlchemy ORM with SQLite

The benchmark uses SQLAlchemy's declarative ORM over SQLite for several reasons:

1. **Single-file persistence**: SQLite stores the entire game state in one file, making runs portable and inspectable
2. **Transactional safety**: ACID guarantees prevent partial state updates
3. **Query flexibility**: SQL allows complex queries for financial reports, task filtering, etc.
4. **Dual-backend support**: The same ORM works with PostgreSQL via `DATABASE_URL` environment variable for production/scaling scenarios

## Schema Overview

```
┌──────────────┐     ┌───────────────────┐
│   Company    │────<│  CompanyPrestige   │  (1 per domain × company)
└──────┬───────┘     └───────────────────┘
       │
       ├────<┌──────────────┐     ┌──────────────────┐
       │     │   Employee   │────<│ EmployeeSkillRate │  (1 per domain × employee)
       │     └──────┬───────┘     └──────────────────┘
       │            │
       │            │    ┌────────────────┐
       │            └───<│ TaskAssignment  │  (employee ↔ task junction)
       │                 └────────┬───────┘
       │                         │
       ├────<┌──────────┐────────┘
       │     │   Task   │────<┌─────────────────┐
       │     └──────────┘     │ TaskRequirement  │  (1 per domain × task)
       │                      └─────────────────┘
       │
       ├────<┌──────────────┐
       │     │  SimEvent    │  (discrete events queue)
       │     └──────────────┘
       │
       ├────<┌──────────────┐
       │     │ LedgerEntry  │  (financial transactions)
       │     └──────────────┘
       │
       ├────<┌──────────────┐
       │     │  SimState    │  (simulation clock & counters)
       │     └──────────────┘
       │
       └────<┌──────────────┐
             │  Scratchpad  │  (agent persistent memory)
             └──────────────┘
```

## Model Details

### Company (`models/company.py`)

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID (PK) | Auto-generated |
| `name` | String | Company name |
| `funds_cents` | BigInteger | Financial balance in cents |

**Design choice**: Funds stored in cents (integer) to avoid floating-point rounding errors in financial calculations. BigInteger supports very large/negative values.

### CompanyPrestige (`models/company.py`)

| Column | Type | Notes |
|--------|------|-------|
| `company_id` | UUID (FK) | References Company |
| `domain` | String | research / inference / data_environment / training |
| `prestige_level` | Float | Range [1.0, 10.0] |

**Design choice**: Prestige is tracked per-domain rather than as a single score. This forces specialization trade-offs and creates a 4-dimensional progression space.

### Employee (`models/employee.py`)

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID (PK) | Auto-generated |
| `company_id` | UUID (FK) | References Company |
| `name` | String | Employee name |
| `tier` | String | junior / mid / senior |
| `work_hours_per_day` | Float | Hours available per business day |
| `salary_cents` | BigInteger | Monthly salary in cents |

### EmployeeSkillRate (`models/employee.py`)

| Column | Type | Notes |
|--------|------|-------|
| `employee_id` | UUID (FK) | References Employee |
| `domain` | String | One of 4 domains |
| `rate_domain_per_hour` | Float | Work units produced per hour |

**Design choice**: Skill rates are **hidden from the agent**. The agent sees tier and salary but not per-domain effectiveness. This creates an information asymmetry puzzle -- the agent must infer employee strengths from task outcomes.

### Task (`models/task.py`)

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID (PK) | Auto-generated |
| `company_id` | UUID (FK, nullable) | NULL = market task, set on acceptance |
| `status` | Enum | market → planned → active → completed_success / completed_fail / cancelled |
| `title` | String | Task description |
| `required_prestige` | Float | Minimum prestige needed in ALL task domains |
| `reward_funds_cents` | BigInteger | Payment on successful completion |
| `reward_prestige_delta` | Float | Prestige gained per domain on success |
| `skill_boost_pct` | Float | Employee skill rate increase on success |
| `accepted_at` | DateTime (nullable) | When task was accepted from market |
| `deadline` | DateTime (nullable) | Calculated at acceptance |
| `completed_at` | DateTime (nullable) | When task finished |
| `success` | Boolean (nullable) | True = on-time, False = late |
| `progress_milestone_pct` | Float | Tracks progress milestones (e.g., 50%) |

**Design choice**: `company_id` being nullable elegantly distinguishes market tasks (available for browsing) from accepted tasks (owned by the company).

### TaskRequirement (`models/task.py`)

| Column | Type | Notes |
|--------|------|-------|
| `task_id` | UUID (FK) | References Task |
| `domain` | String | Which domain this requirement covers |
| `required_qty` | Float | Total work units needed |
| `completed_qty` | Float | Work units completed so far |

**Design choice**: Multi-domain requirements make tasks a multi-dimensional optimization problem. A task might need work in 2-4 domains simultaneously.

### TaskAssignment (`models/task.py`)

| Column | Type | Notes |
|--------|------|-------|
| `task_id` | UUID (FK) | References Task |
| `employee_id` | UUID (FK) | References Employee |
| `assigned_at` | DateTime | When assigned |

**Design choice**: Many-to-many junction table. An employee can work on multiple tasks (throughput splits), and a task can have multiple employees (parallel progress).

### SimEvent (`models/event.py`)

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID (PK) | Deterministic (uuid5) |
| `company_id` | UUID (FK) | References Company |
| `event_type` | String | task_completed / bankruptcy / task_half / horizon_end |
| `scheduled_at` | DateTime | When event triggers |
| `payload` | JSON | Event-specific data |
| `dedupe_key` | String | Prevents duplicate events |
| `consumed` | Boolean | True after processing |

### LedgerEntry (`models/ledger.py`)

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID (PK) | Auto-generated |
| `company_id` | UUID (FK) | References Company |
| `occurred_at` | DateTime | Transaction timestamp |
| `category` | Enum | MONTHLY_PAYROLL / TASK_REWARD / TASK_FAIL_PENALTY / TASK_CANCEL_PENALTY |
| `amount_cents` | BigInteger | Signed amount (negative = cost) |
| `ref_type` | String (nullable) | Reference entity type |
| `ref_id` | UUID (nullable) | Reference entity ID |

**Design choice**: Immutable append-only ledger provides a complete financial audit trail. No entries are ever deleted or modified.

### SimState (`models/sim_state.py`)

| Column | Type | Notes |
|--------|------|-------|
| `company_id` | UUID (FK, PK) | References Company |
| `sim_time` | DateTime | Current simulation clock |
| `run_seed` | Integer | RNG seed for reproducibility |
| `horizon_end` | DateTime | When simulation ends |
| `replenish_counter` | Integer | Tracks market task replenishment |

### Scratchpad (`models/scratchpad.py`)

| Column | Type | Notes |
|--------|------|-------|
| `company_id` | UUID (FK) | References Company |
| `content` | Text | Free-form agent notes |

**Design choice**: Scratchpad survives LLM context truncation, giving the agent persistent memory across the full simulation.

## Session Management (`session.py`)

```python
session_scope(factory) → context manager
```

- Creates a scoped session with automatic commit/rollback
- Supports both SQLite (default) and PostgreSQL (via `DATABASE_URL`)
- `init_db()` creates all tables from ORM metadata

**Design choice**: Context manager pattern ensures every database operation is properly transacted, preventing partial state updates that would corrupt the simulation.
