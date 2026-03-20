# Client Trust & Loyalty

## The Big Idea

Every client has a **hidden loyalty score** the agent can't see. Some clients are loyal (investing in them pays off), some are adversarial "RATs" (investing in them backfires). The agent has to figure out which is which from observed behavior — deadline failures from scope creep, not explicit labels.

This tests:

1. **Can the agent spot patterns?** RATs look normal. The only signal is that tasks from them fail deadlines disproportionately.
2. **Can the agent cut losses?** Dropping a RAT means lost trust investment. Keeping one means repeated deadline failures and prestige loss.

## How Trust Works

Every client starts at trust 0. Completing tasks builds trust (0-5 scale). Trust gives two benefits:

- **Work reduction**: Up to 35-40% less work per task at max trust (trusted clients give clearer specs)
- **Gated tasks**: ~20-30% of high-reward tasks require minimum trust to accept

Trust decays daily and drops on failure/cancellation. Working for Client A erodes trust with all other clients (cross-client decay), so you can't maintain trust with everyone — you have to pick 2-3 clients to focus on.

Note: Trust does NOT affect task reward amounts. Reward multiplier was removed — only work reduction remains. The revenue benefit of trust is indirect: faster task completion → more tasks per month → more revenue.

## How Loyalty Works

At world generation, a fixed number of RATs are guaranteed: `round(num_clients × loyalty_rat_fraction)`, minimum 1. RATs get loyalty in [-1.0, -0.3], non-RATs get loyalty in [-0.3, 1.0].

- **Loyal** (> 0.3): Trust investment pays off via work reduction.
- **Neutral** (-0.3 to 0.3): No special effects.
- **RAT** (< -0.3): Adversarial. Looks normal, causes scope creep on accepted tasks.

Employees and clients use a **fixed world seed** (seed=1) so the same clients (including the same RATs) appear across all run seeds. Only task generation varies by seed.

The agent never sees loyalty scores. It only sees: client name, tier, specialties, trust level.

## What RATs Do: Scope Creep

When the agent accepts a task from a RAT client, the **actual work required is secretly inflated** after acceptance — but the deadline is calculated from the original (smaller) amount. The task looks completable when browsing but isn't.

```
inflation = scope_creep_max × |loyalty|
inflation = max(1.3, inflation)  # minimum 130% inflation ensures deadline failure
for each requirement:
    required_qty *= (1 + inflation)
```

- **Scope creep formula**: `scope_creep_max = loyalty_severity × 1.0`
- **At medium (severity=1.0)**: A RAT with loyalty=-0.7 inflates work by 130% (minimum floor)
- **Effect**: RAT tasks always miss deadlines → zero reward + prestige penalty

Scope creep activates from the first task (no trust threshold needed). The agent can detect it by noticing that tasks from certain clients consistently fail despite looking feasible in the market.

Payment disputes were removed — scope creep alone provides sufficient RAT damage.

## How the Agent Can Detect RATs

The agent has one tool: `yc-bench client history`. This shows per-client:

- Tasks succeeded and failed count
- `failure_rate_pct` per client

An agent that periodically checks history will notice a client whose tasks fail deadlines more than others (scope creep signal). An agent that never checks will keep getting exploited.

Additionally, the agent can observe via `task inspect` that the `required_qty` is larger than what was listed in `market browse` — a direct scope creep signal if the agent compares pre-accept and post-accept values.

## Config Knobs

| Knob                   | Medium | Hard | Nightmare |
| ---------------------- | ------ | ---- | --------- |
| `loyalty_rat_fraction` | 0.20   | 0.20 | 0.25      |
| `loyalty_severity`     | 1.0    | 0.7  | 0.9       |
| `loyalty_reveal_trust` | 0.0    | 1.5  | 1.0       |

Derived from severity:

- `scope_creep_max = severity × 1.0`
