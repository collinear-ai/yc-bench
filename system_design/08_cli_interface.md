# CLI Interface

**Location**: `src/yc_bench/cli/`

## Overview

The CLI is the agent's sole interface to the simulation. Every command returns structured JSON, enabling reliable parsing by LLMs.

## Design Choices

### JSON-Only Output

All CLI commands return JSON, never free-text:

```bash
$ yc-bench company status
{
  "company_name": "Nexus AI",
  "funds": "$150,000.00",
  "funds_cents": 15000000,
  "monthly_payroll": "$30,000.00",
  "runway_months": 5.0,
  "prestige": {
    "research": 3.5,
    "inference": 2.1,
    "data_environment": 1.0,
    "training": 4.2
  }
}
```

**Why JSON?**
- Unambiguous parsing by LLMs (vs. formatted tables)
- Consistent structure across all commands
- Easy to pipe into `python_repl` for analysis
- Machine-readable without regex or text parsing

### Command Group Organization

| Group | File | Purpose |
|-------|------|---------|
| `company` | `company_commands.py` | Company status, prestige overview |
| `employee` | `employee_commands.py` | Employee listing and details |
| `market` | `market_commands.py` | Browse available tasks |
| `task` | `task_commands.py` | Task lifecycle (accept/assign/dispatch/cancel/inspect/list) |
| `sim` | `sim_commands.py` | Simulation control (resume) |
| `finance` | `finance_commands.py` | Ledger queries |
| `report` | `report_commands.py` | Monthly P&L reports |
| `scratchpad` | `scratchpad_commands.py` | Persistent agent memory |

**Design choice**: Command groups mirror real business functions (operations, HR, finance, strategy). This makes the interface intuitive for LLM agents that have been trained on business concepts.

## Command Details

### Company Commands

#### `company status`
Returns current funds, payroll, runway, and prestige levels per domain.

**Design choice**: Single command gives the agent a complete financial and strategic snapshot. Reduces the number of API calls needed per decision cycle.

### Employee Commands

#### `employee list`
Returns all employees with tier, salary, and current active task count.

**Design choice**: Shows active task count but NOT skill rates. The agent must infer capabilities.

### Market Commands

#### `market browse [--domain X] [--reward-min-cents N] [--offset O] [--limit L]`
Browse available market tasks with optional filters. Results are capped at `market_browse_default_limit` (default 50) per page.

The browse **auto-filters** by prestige and trust: only tasks the company can actually accept are shown. This means:
- Per-domain prestige check: all required domains must meet the task's `required_prestige`
- Trust check: company must have sufficient trust with the task's client

**Design choice**: Auto-filtering prevents the agent from wasting turns trying to accept inaccessible tasks. Pagination (`--offset`) allows browsing beyond the first page.

### Task Commands

#### `task accept <task_id>`
Accept a market task. Validates prestige requirements. Sets deadline.

#### `task assign <task_id> <employee_id>`
Assign an employee to a planned/active task. Recalculates ETAs.

#### `task dispatch <task_id>`
Start work on a planned task. Changes status to active.

#### `task cancel <task_id>`
Cancel a task. Applies prestige penalty. Frees employees.

#### `task inspect <task_id>`
Detailed view of a single task: requirements, progress, assignments, deadline.

#### `task list [--status X]`
List company tasks with optional status filter.

**Design choice**: The accept → assign → dispatch flow gives the agent explicit control over each phase. This mirrors real project management where you scope, staff, and then kick off work.

### Simulation Commands

#### `sim resume`
Advance simulation to the next event. Returns wake events.

```json
{
  "advanced_to": "2025-02-15T09:00:00",
  "wake_events": [
    {"type": "task_completed", "task_id": "...", "success": true},
    {"type": "payroll", "amount": -3000000}
  ]
}
```

**Design choice**: Resume is the only way to advance time. The agent explicitly chooses when to move forward, creating natural decision checkpoints.

### Finance Commands

#### `finance ledger [--category X] [--from DATE] [--to DATE] [--offset O] [--limit L]`
Query the immutable transaction history.

**Design choice**: Full ledger access lets sophisticated agents analyze spending patterns and project future cash flow.

### Report Commands

#### `report monthly`
Aggregated P&L by month.

**Design choice**: Monthly reports provide a higher-level financial view than raw ledger entries, useful for strategic planning.

### Scratchpad Commands

#### `scratchpad read`
Read persistent notes.

#### `scratchpad write <content>`
Overwrite scratchpad contents.

#### `scratchpad append <content>`
Add to existing scratchpad.

#### `scratchpad clear`
Clear scratchpad.

**Design choice**: The scratchpad is critical for long simulations where LLM context gets truncated. The agent can store:
- Employee capability observations
- Strategic plans
- Financial projections
- Task priority lists

This compensates for context window limitations and tests whether the agent proactively maintains external memory.

## Error Handling

All commands return structured errors:

```json
{
  "error": "Insufficient prestige in research (have 2.3, need 4.0)"
}
```

**Design choice**: Descriptive error messages help the agent understand what went wrong and adjust its strategy, rather than failing silently or with cryptic messages.

## CLI Entry Point (`__main__.py`)

The CLI uses a command-line parser (likely Click or argparse) to route commands to handler functions. Each handler:

1. Opens a database session
2. Validates inputs
3. Performs the operation
4. Returns JSON output
5. Commits or rolls back the transaction

**Design choice**: Each CLI call is a self-contained transaction. This prevents partial state updates and ensures the simulation remains consistent.
