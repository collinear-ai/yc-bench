# Configuration System

**Location**: `src/yc_bench/config/`

## Overview

The configuration system uses Pydantic models validated from TOML preset files. It controls every aspect of the simulation: world generation parameters, difficulty tuning, agent behavior, and distribution specifications.

## Design Choices

### Pydantic Schema (`schema.py`)

The configuration hierarchy:

```
ExperimentConfig
├── AgentConfig          # LLM model, tools, retry settings
├── LoopConfig           # Turn budget, auto-resume threshold
├── SimConfig            # Simulation parameters
└── WorldConfig          # World generation parameters
    ├── CompanyConfig     # Initial funds, starting prestige
    ├── EmployeeConfig    # Team size, tier distribution, salary ranges
    ├── TaskConfig        # Task count, domain requirements, deadlines
    └── PrestigeConfig    # Decay rate, penalty multipliers, scaling
```

**Why Pydantic?**
- Type validation at load time (catch config errors early)
- Default values with optional overrides
- Discriminated unions for distribution specs
- Clear documentation through type annotations
- Serialization to/from TOML/JSON

### TOML Preset Files (`presets/`)

```toml
# medium.toml
[world]
initial_funds_cents = 500_000_00

[world.prestige]
decay_per_day = 0.005
penalty_fail_multiplier = 0.8
penalty_cancel_multiplier = 1.0

[world.tasks]
count = 200
deadline_qty_per_day = 11.0

[world.tasks.reward_funds]
type = "triangular"
min = 5000_00
mode = 15000_00
max = 50000_00
```

**Why TOML?** Human-readable, supports comments, natural hierarchy via sections, widely supported in Python. Better than JSON for config files (comments), simpler than YAML (fewer gotchas).

### Preset Hierarchy

| Preset | Focus | Key Characteristics |
|--------|-------|-------------------|
| `default.toml` | Base | All defaults; other presets override selectively |
| `tutorial.toml` | Learning | Relaxed deadlines, prestige-1 tasks only, high funds |
| `easy.toml` | Casual | Relaxed deadlines, flat prestige requirements |
| `medium.toml` | Standard | Prestige climbing, 2-domain tasks, 9-day deadlines |
| `hard.toml` | Challenge | Prestige gating active, 7-day deadlines, 1.5x cancel penalty |
| `nightmare.toml` | Extreme | Razor-thin margins, 6-day deadlines, 2x penalties |

**Design choice**: Preset-based difficulty rather than a single "difficulty slider" allows fine-grained control. Each preset can tune dozens of independent parameters.

### Config Loading (`loader.py`)

```python
def load_config(preset_name: str) -> ExperimentConfig:
    base = load_toml("default.toml")
    overlay = load_toml(f"{preset_name}.toml")
    merged = deep_merge(base, overlay)
    return ExperimentConfig(**merged)
```

**Design choice**: Config inheritance via deep merge. Presets only specify what differs from default, keeping preset files concise and maintainable.

## Distribution Specifications (`sampling.py`)

### The DistSpec System

Many world generation parameters use statistical distributions rather than fixed values:

```python
class DistSpec(BaseModel):
    """Discriminated union of distribution types."""
    type: Literal["triangular", "beta", "normal", "uniform", "constant"]
    # Parameters vary by type
```

**Supported distributions:**

| Type | Parameters | Use Case |
|------|-----------|----------|
| `triangular` | min, mode, max | Task rewards, skill rates (natural asymmetric bell curve) |
| `beta` | alpha, beta, scale | Prestige requirements (skewed toward low values) |
| `normal` | mean, std | Symmetric variation around a target |
| `uniform` | low, high | Equal probability across range |
| `constant` | value | Fixed value (no randomness) |

**Why discriminated unions?** Pydantic validates the correct parameters for each distribution type at load time. Invalid combinations (e.g., triangular with alpha parameter) are caught before the simulation runs.

### Usage Example

```toml
[world.tasks.reward_funds]
type = "triangular"
min = 5000_00
mode = 15000_00
max = 50000_00

[world.employees.junior_rate]
type = "beta"
alpha = 2.0
beta = 5.0
scale = 3.0
```

## World Generation

### Seeding (`services/seed_world.py`)

```python
def seed_world_transactional(session, cfg, seed):
    rng = create_rng(seed)
    company = create_company(session, cfg.world.company)
    employees = generate_employees(session, company, cfg.world.employees, rng)
    tasks = generate_tasks(session, cfg.world.tasks, rng)
    sim_state = create_sim_state(session, company, cfg.sim, seed)
```

**Design choice**: Single-transaction world seeding ensures atomic creation. Either the entire world is created or nothing is -- no partial states.

### Employee Generation (`services/generate_employees.py`)

1. Generate N employees (default 10)
2. Assign tiers from configured distribution (e.g., 30/40/30 junior/mid/senior)
3. For each employee, sample 4 skill rates from per-tier distributions
4. Set salary based on tier range

### Task Generation (`services/generate_tasks.py`)

1. Generate M tasks (default 200+)
2. First 10 tasks are always prestige-1 (guaranteed accessible)
3. Remaining tasks have stratified prestige requirements
4. Each task gets 2-4 domain requirements sampled from distributions
5. Rewards scale with prestige and task size

**Design choice**: Stratified generation ensures:
- The agent always has starting tasks (prestige-1 guaranteed)
- Tasks span the full prestige range (progression is possible)
- No prestige "dead zones" where no tasks exist

### RNG Management (`services/rng.py`)

```python
def create_rng(seed: int) -> numpy.random.Generator:
    return numpy.random.default_rng(seed)
```

**Design choice**: Centralized RNG with explicit seed ensures full reproducibility. Same seed → same world → same event sequence (given same agent actions).

## Key Configuration Parameters

### Financial Tuning

| Parameter | Default | Effect |
|-----------|---------|--------|
| `initial_funds_cents` | 500,000 | Starting capital |
| `reward_prestige_scale` | 0.15 | How much prestige amplifies rewards |
| `salary_bump_pct` | 1.0 | Per-completion salary increase |

### Prestige Tuning

| Parameter | Default | Effect |
|-----------|---------|--------|
| `prestige_decay_per_day` | 0.005 | Daily prestige loss |
| `penalty_fail_multiplier` | 0.8 | Prestige cost of late completion |
| `penalty_cancel_multiplier` | 1.0 | Prestige cost of cancellation |
| `prestige_min` | 1.0 | Floor value |
| `prestige_max` | 10.0 | Ceiling value |

### Task Tuning

| Parameter | Default | Effect |
|-----------|---------|--------|
| `deadline_qty_per_day` | 11.0 | Deadline generosity |
| `num_domains_per_task` | 2-4 | Multi-domain complexity |
| `progress_milestone_pct` | 50 | When to fire halfway event |

### Agent Tuning

| Parameter | Default | Effect |
|-----------|---------|--------|
| `max_turns` | 500 | Hard turn limit |
| `max_turns_without_resume` | 5 | Auto-resume threshold |
| `history_truncation` | 50 | Turns kept in context |
