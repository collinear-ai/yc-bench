# Prestige System

**Location**: `src/yc_bench/db/models/company.py` (CompanyPrestige), `src/yc_bench/core/engine.py` (decay), `src/yc_bench/core/handlers/task_complete.py` (rewards/penalties)

## Overview

Prestige is YC-Bench's core progression mechanic. It controls access to higher-tier tasks (which offer better rewards) and decays over time, forcing continuous engagement.

## Design Choices

### Per-Domain Prestige (4 Independent Tracks)

```
research:          ████████░░  (8.0)
inference:         ██████░░░░  (6.0)
data_environment:  ███░░░░░░░  (3.0)
training:          █████░░░░░  (5.0)
```

**Why 4 domains?** This creates a 4-dimensional strategic space:
- The agent can't max all domains simultaneously (decay + limited employees)
- Specialization unlocks high-tier tasks in 1-2 domains
- Diversification provides resilience but slower progression
- Multi-domain tasks require balanced prestige across their domains

### Prestige Range: [1.0, 10.0]

| Level | Meaning |
|-------|---------|
| 1.0 | Minimum (starting/decayed) |
| 3.0-4.0 | Mid-tier tasks accessible |
| 7.0-8.0 | High-tier tasks accessible |
| 10.0 | Maximum (hard cap) |

**Design choice**: The 1-10 range is intuitive and provides enough granularity for meaningful gating tiers without over-complicating the system.

## Prestige Gain

On successful task completion (on-time):

```
for each domain in task.requirements:
    company_prestige[domain] += task.reward_prestige_delta
    company_prestige[domain] = min(prestige, 10.0)  # cap
```

**Design choice**: Prestige gain is per-domain and tied to the task's requirements. Completing a research+inference task only boosts those two domains, not training or data_environment.

### Prestige Scaling of Rewards

```
actual_reward = base_reward × (1 + reward_prestige_scale × (prestige - 1))
```

Higher prestige in a domain means better financial returns from tasks in that domain. This creates a virtuous cycle: more prestige → more money → more capacity → more prestige.

## Prestige Loss

### Decay (Daily)

```
prestige -= decay_per_day × days_elapsed
prestige = max(prestige, 1.0)  # floor
```

Default decay rate: -0.005/day. This is slow enough to not punish short gaps but fast enough that inactive domains eventually return to baseline.

**Design choice**: Continuous decay prevents "build once, exploit forever" strategies. The agent must continuously complete tasks in a domain to maintain access.

### Failure Penalty

On late task completion:

```
for each domain in task.requirements:
    company_prestige[domain] -= task.reward_prestige_delta × fail_multiplier
    company_prestige[domain] = max(prestige, 1.0)
```

Default `fail_multiplier`: 0.8. Late completion costs almost as much prestige as success would have gained.

### Cancel Penalty

On task cancellation:

```
for each domain in task.requirements:
    company_prestige[domain] -= task.reward_prestige_delta × cancel_multiplier
    company_prestige[domain] = max(prestige, 1.0)
```

Cancel multipliers vary by difficulty (higher on hard/nightmare).

## Prestige Gating

Tasks have a `required_prestige` field. At task acceptance:

```python
for domain in task.requirements:
    if company_prestige[domain] < task.required_prestige:
        reject()  # must meet prestige in ALL task domains
```

**Design choice**: Per-domain gating means a task with `required_prestige=5.0` and requirements in research + training needs prestige >= 5.0 in BOTH research AND training. This prevents gaming.

### Stratified Market Tasks

The first 10 market tasks are always prestige-1 (accessible immediately). Higher prestige tasks are introduced with stratified distribution. This ensures:

- The agent always has something to work on initially
- Progression is visible (new tasks unlock as prestige grows)
- No dead-end states where the agent can't accept any task

## Strategic Implications

The prestige system creates several key strategic tensions:

1. **Specialize vs. Diversify**: Focus on 1-2 domains for deep access, or spread across all 4?
2. **Risk vs. Reward**: High-prestige tasks pay more but failure costs more prestige
3. **Maintenance vs. Growth**: Should the agent keep working in mastered domains (maintenance) or push new ones (growth)?
4. **Accept vs. Defer**: Taking a task you might fail risks prestige loss; waiting risks decay

These tensions make the benchmark more than just "do tasks fast" -- it tests genuine strategic reasoning.
