# Client Trust & Loyalty

## The Big Idea

Every client has a **hidden loyalty score** the agent can't see. Some clients are loyal (investing in them pays off), some are adversarial "RATs" (investing in them backfires). The agent has to figure out which is which from observed behavior — delayed consequences, not explicit labels.

This tests three things:

1. **Can the agent invest under uncertainty?** You don't know if a client is worth it until you've sunk 10+ tasks into them.
2. **Can the agent spot patterns?** RATs look normal at first. The only signal is that tasks from them fail deadlines more often and money sometimes disappears after completion.
3. **Can the agent cut losses?** Dropping a RAT costs the trust you built. Keeping one costs real money.

## How Trust Works

Every client starts at trust 0. Completing tasks builds trust (0-5 scale). Trust gives two benefits:

- **Work reduction**: Up to 40% less work per task at max trust (loyal clients give clearer specs)
- **Gated tasks**: ~20% of high-reward tasks require minimum trust to accept

Trust decays daily and drops on failure/cancellation. Working for Client A erodes trust with all other clients (cross-client decay), so you can't maintain trust with everyone — you have to pick 2-3 clients to focus on.

## How Loyalty Works

At world generation, each client gets a hidden loyalty score from `triangular(-1, 1, mode≈0.6)`:

- **Loyal** (> 0.3): ~50% of clients. Trust investment pays off via work reduction.
- **Neutral** (-0.3 to 0.3): ~35%. No special effects.
- **RAT** (< -0.3): ~15%. Adversarial. Looks normal, exploits you at higher trust.

The agent never sees loyalty scores. It only sees: client name, tier, specialties, trust level.

## What RATs Do

RAT effects activate once trust exceeds `loyalty_reveal_trust` (default 0.5 for medium). The effects scale with `|loyalty| × sqrt(trust_fraction)` — sqrt scaling means they bite early and plateau, rather than being negligible until max trust.

### 1. Scope Creep (Bait-and-Switch)

When you accept a task from a RAT at sufficient trust, the **actual work required is secretly inflated** — but the deadline is calculated from the original (smaller) amount. The task looks completable but isn't.

- **Max inflation**: `severity × 0.70` (medium: 56%)
- **Effect**: Tasks from RATs miss deadlines more often. The agent notices when progress milestones arrive later than expected.

### 2. Payment Disputes (Delayed Clawback)

After completing a RAT's task, there's a random chance a `PAYMENT_DISPUTE` event fires 2-7 days later, clawing back a chunk of the reward.

- **Max clawback**: `severity × 0.80` of the reward (medium: 64%)
- **Max probability**: `severity × 0.50` per task (medium: 40%)
- **Effect**: The agent gets paid, then days later money disappears. The only way to notice is checking `client history` and seeing listed rewards don't match received amounts.

### 3. Work Reduction for Loyal Clients

Loyal clients reduce required work by `trust_work_reduction_max × trust / trust_max`. This is the payoff for choosing well — loyal clients make tasks faster, meaning more tasks, more revenue.

## Intensity Scaling

All RAT effects use the same intensity formula:

```
trust_fraction = (trust - threshold) / (max_trust - threshold)
intensity = |loyalty| × sqrt(trust_fraction)
```

The sqrt makes effects noticeable early (trust barely above threshold) rather than negligible until max trust. At medium difficulty with a RAT (loyalty -0.57) at trust 2.0:


| Effect              | Value                  |
| ------------------- | ---------------------- |
| Scope creep         | +18% work inflation    |
| Dispute probability | 13% per completed task |
| Clawback amount     | up to 12% of reward    |


## How the Agent Can Detect RATs

The agent has one tool: `yc-bench client history`. This shows per-client:

- Tasks completed (success/fail count)
- Listed reward total vs net received (after disputes)
- Dispute count

An agent that periodically checks history will notice:

- A client whose tasks fail deadlines more than others (scope creep)
- A client where net received < listed rewards (disputes)

An agent that never checks will keep getting exploited.

## Config Knobs


| Knob                   | Medium | Hard | Nightmare |
| ---------------------- | ------ | ---- | --------- |
| `loyalty_rat_fraction` | 0.15   | 0.20 | 0.25      |
| `loyalty_severity`     | 0.8    | 0.7  | 0.9       |
| `loyalty_reveal_trust` | 0.5    | 1.5  | 1.0       |


Derived from severity:

- `scope_creep_max = severity × 0.70`
- `dispute_clawback_max = severity × 0.80`
- `dispute_prob_max = severity × 0.50`

