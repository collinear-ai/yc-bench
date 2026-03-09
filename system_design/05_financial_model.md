# Financial Model

**Location**: `src/yc_bench/db/models/ledger.py`, `src/yc_bench/cli/finance_commands.py`, `src/yc_bench/cli/report_commands.py`, `src/yc_bench/core/handlers/`

## Overview

The financial model simulates a startup's cash flow: revenue from completed tasks, costs from employee payroll, and penalties for failures. Running out of money triggers bankruptcy and ends the simulation.

## Design Choices

### Cents-Based Integer Arithmetic

All financial values are stored as `BigInteger` in cents:

```
$1,000.00 = 100_000 cents
```

**Why cents?** Floating-point arithmetic introduces rounding errors that compound over hundreds of transactions. Integer cents guarantee exact financial accounting -- critical for a deterministic benchmark.

### Immutable Append-Only Ledger

Every financial transaction creates a `LedgerEntry` that is never modified or deleted:

```python
class LedgerEntry:
    category: MONTHLY_PAYROLL | TASK_REWARD | TASK_FAIL_PENALTY | TASK_CANCEL_PENALTY
    amount_cents: int  # negative for costs, positive for revenue
    occurred_at: datetime
    ref_type: str      # optional reference to source entity
    ref_id: UUID       # optional reference ID
```

**Why immutable?** An append-only ledger provides:
- Complete audit trail for debugging
- Ability to reconstruct balance at any point in time
- No risk of silent data corruption
- Natural fit for the `finance ledger` and `report monthly` CLI commands

## Revenue Sources

### Task Rewards

On successful (on-time) completion:

```
reward = base_reward × (1 + prestige_scale × (avg_prestige - 1))
```

Where `avg_prestige` is averaged across the task's required domains. Higher prestige = higher payouts.

**Design choice**: Prestige-scaled rewards create a positive feedback loop that mirrors real business dynamics -- reputation leads to better opportunities.

### Revenue Timing

Rewards are credited immediately upon task completion (when the `task_completed` event fires with `success=True`).

## Cost Sources

### Monthly Payroll

Payroll is deducted on the **first business day** of each month:

```
total_payroll = sum(employee.salary_cents for all employees)
```

**Design choice**: Monthly payroll creates predictable but unavoidable costs. The agent must maintain positive cash flow to cover it.

### Salary Bumps

Each completed task increases salaries:

```
for each assigned employee:
    salary_cents *= 1.01  # 1% increase per completion
```

**Design choice**: Compounding salary increases mean success has a hidden cost. Long-running simulations see payroll grow substantially, creating late-game financial pressure even as task rewards scale with prestige.

### Failure Penalties

Late task completion incurs no direct financial penalty beyond the missed reward opportunity. However, the prestige loss from failure reduces future reward scaling.

### Cancel Penalties

Cancellation may incur a financial penalty depending on configuration (some presets charge a fraction of the reward).

## Payroll-Event Tie-Breaking

When payroll and events fall on the same timestamp:

```
Payroll is processed BEFORE events
```

**Design choice**: This ordering is critical. If a task completes on the same day as payroll:
1. Payroll deducts first (may push funds negative)
2. Task completion reward credits (may save from bankruptcy)
3. Bankruptcy check happens after both

This gives the agent the benefit of the doubt -- a task completing on payday can save the company.

## Bankruptcy

Bankruptcy triggers when `funds_cents < 0` after payroll processing:

```python
if company.funds_cents < 0:
    insert_bankruptcy_event(session, company_id, sim_time)
```

**Design choice**: Bankruptcy is checked only after payroll (not after penalties). This simplifies the model and makes payroll the primary survival constraint.

### Bankruptcy as Terminal State

Once bankruptcy fires, the simulation ends. There is no recovery mechanic.

**Why no bailout?** The benchmark tests whether the agent can sustainably manage a business. Allowing recovery would dilute this signal.

## Financial Reports

### Ledger Query (`finance ledger`)

The agent can query the full transaction history with filters:
- Category filter
- Date range filter
- Pagination

### Monthly P&L (`report monthly`)

Aggregates transactions by month:

```
Month     Revenue    Payroll    Penalties    Net
2025-01   $50,000    $30,000    $0           $20,000
2025-02   $35,000    $30,300    $5,000       -$300
```

**Design choice**: Structured financial reporting gives the agent the data it needs to make informed decisions about task selection and resource allocation.

## Runway Calculation

The `company status` command includes a runway estimate:

```
runway_months = funds_cents / monthly_payroll_cents
```

This helps the agent gauge urgency. Low runway signals that the agent needs profitable tasks quickly.

## Difficulty Scaling

Financial pressure scales with difficulty preset:

| Preset | Initial Funds | Payroll Pressure | Penalties |
|--------|--------------|-----------------|-----------|
| tutorial | Very high | Low | Minimal |
| easy | High | Moderate | Low |
| medium | Moderate | Moderate | Standard |
| hard | Low | High | 1.5x |
| nightmare | Very low | Very high | 2x |
