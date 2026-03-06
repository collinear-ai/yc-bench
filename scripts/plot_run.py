"""Plot a benchmark run: funds over time, prestige evolution, task outcomes."""
import os
import sys
from decimal import Decimal
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.dates as mdates
import numpy as np

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/yc_bench")
sys.path.insert(0, str(Path(__file__).parent))

from src.bench.db.session import build_engine, build_session_factory, session_scope
from src.bench.db.models.ledger import LedgerEntry, LedgerCategory
from src.bench.db.models.task import Task, TaskRequirement, TaskStatus
from src.bench.db.models.company import CompanyPrestige

engine = build_engine()
factory = build_session_factory(engine)

DOMAIN_COLORS = {
    "research":         "#3498db",
    "inference":        "#9b59b6",
    "data_environment": "#1abc9c",
    "training":         "#e67e22",
}

with session_scope(factory) as db:
    # --- Ledger: reconstruct running balance ---
    entries = (
        db.query(LedgerEntry)
        .order_by(LedgerEntry.occurred_at)
        .all()
    )
    initial_funds = 25_000_000
    times, balances, categories = [], [], []
    running = initial_funds
    for e in entries:
        running += int(e.amount_cents)
        times.append(e.occurred_at)
        balances.append(running / 100)
        categories.append(e.category)

    # --- Tasks ---
    tasks = (
        db.query(Task)
        .filter(Task.completed_at.isnot(None))
        .order_by(Task.completed_at)
        .all()
    )
    task_times, task_rewards, task_success, task_prestige = [], [], [], []
    for t in tasks:
        task_times.append(t.completed_at)
        task_rewards.append(int(t.reward_funds_cents) / 100)
        task_success.append(t.status == TaskStatus.COMPLETED_SUCCESS)
        task_prestige.append(t.required_prestige)

    # --- Prestige per domain (sampled from task completions) ---
    # Build prestige history by replaying prestige deltas
    from src.bench.db.models.task import TaskRequirement
    from src.bench.db.models.company import Domain
    prestige_history = {d.value: [(times[0] if times else None, 1.0)] for d in Domain}

    completed = [t for t in tasks if t.completed_at]
    completed.sort(key=lambda t: t.completed_at)

    current_prestige = {d.value: 1.0 for d in Domain}
    for t in completed:
        reqs = db.query(TaskRequirement).filter(TaskRequirement.task_id == t.id).all()
        for req in reqs:
            d = req.domain.value
            if t.status == TaskStatus.COMPLETED_SUCCESS:
                current_prestige[d] = min(10.0, current_prestige[d] + float(t.reward_prestige_delta))
            else:
                penalty = 1.4 * float(t.reward_prestige_delta)
                current_prestige[d] = max(1.0, current_prestige[d] - penalty)
            prestige_history[d].append((t.completed_at, current_prestige[d]))

    # Final prestige from DB
    final_prestige = {
        row.domain.value: float(row.prestige_level)
        for row in db.query(CompanyPrestige).all()
    }

# ── Plot ────────────────────────────────────────────────────────────────
fig = plt.figure(figsize=(16, 10), facecolor="#0f1117")
gs = gridspec.GridSpec(2, 2, figure=fig, hspace=0.45, wspace=0.35)

ax_funds    = fig.add_subplot(gs[0, :])   # full width top
ax_prestige = fig.add_subplot(gs[1, 0])
ax_tasks    = fig.add_subplot(gs[1, 1])

for ax in [ax_funds, ax_prestige, ax_tasks]:
    ax.set_facecolor("#1a1d27")
    ax.tick_params(colors="#aaaaaa", labelsize=9)
    for spine in ax.spines.values():
        spine.set_edgecolor("#333344")

# ── Funds over time ──────────────────────────────────────────────────────
payroll_times = [t for t, c in zip(times, categories) if c == LedgerCategory.MONTHLY_PAYROLL]
payroll_vals  = [b for b, c in zip(balances, categories) if c == LedgerCategory.MONTHLY_PAYROLL]
reward_times  = [t for t, c in zip(times, categories) if c == LedgerCategory.TASK_REWARD]
reward_vals   = [b for b, c in zip(balances, categories) if c == LedgerCategory.TASK_REWARD]

ax_funds.plot(times, balances, color="#4fc3f7", linewidth=1.8, zorder=3, label="Balance")
ax_funds.fill_between(times, [b / max(balances) * min(balances) * 0.5 for b in balances],
                      balances, alpha=0.08, color="#4fc3f7", zorder=2)
ax_funds.scatter(reward_times, reward_vals, color="#2ecc71", s=30, zorder=5,
                 label="Task reward", marker="^")
ax_funds.scatter(payroll_times, payroll_vals, color="#e74c3c", s=20, zorder=5,
                 label="Payroll", marker="v", alpha=0.7)

ax_funds.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x/1000:.0f}K" if x < 1_000_000 else f"${x/1_000_000:.1f}M"))
ax_funds.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
ax_funds.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
plt.setp(ax_funds.xaxis.get_majorticklabels(), rotation=30, ha="right")
ax_funds.set_title("Company Funds Over Time", color="white", fontsize=12, pad=8)
ax_funds.set_ylabel("Balance", color="#aaaaaa", fontsize=9)
ax_funds.legend(fontsize=8, facecolor="#1a1d27", edgecolor="#333344",
                labelcolor="white", loc="upper left")
ax_funds.grid(axis="y", color="#333344", linewidth=0.5, linestyle="--")

# ── Prestige evolution ───────────────────────────────────────────────────
for domain, history in prestige_history.items():
    hist_times = [h[0] for h in history if h[0] is not None]
    hist_vals  = [h[1] for h in history if h[0] is not None]
    if len(hist_times) < 2:
        continue
    color = DOMAIN_COLORS.get(domain, "#aaaaaa")
    ax_prestige.step(hist_times, hist_vals, where="post",
                     color=color, linewidth=1.6, label=domain)

ax_prestige.axhline(y=1.0, color="#555566", linewidth=0.8, linestyle=":")
ax_prestige.set_ylim(0.8, 10.5)
ax_prestige.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
ax_prestige.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
plt.setp(ax_prestige.xaxis.get_majorticklabels(), rotation=30, ha="right")
ax_prestige.set_title("Prestige by Domain", color="white", fontsize=12, pad=8)
ax_prestige.set_ylabel("Prestige Level", color="#aaaaaa", fontsize=9)
ax_prestige.legend(fontsize=7.5, facecolor="#1a1d27", edgecolor="#333344",
                   labelcolor="white", ncol=2, loc="upper left")
ax_prestige.grid(axis="y", color="#333344", linewidth=0.5, linestyle="--")

# ── Task outcomes scatter ────────────────────────────────────────────────
if task_times:
    colors = ["#2ecc71" if s else "#e74c3c" for s in task_success]
    scatter = ax_tasks.scatter(
        task_times, task_rewards,
        c=colors, s=[40 + p * 12 for p in task_prestige],
        alpha=0.85, zorder=4, edgecolors="none"
    )
    # Annotate prestige on each dot
    for t, r, p, s in zip(task_times, task_rewards, task_prestige, task_success):
        ax_tasks.annotate(f"p{p}", (t, r), fontsize=6, color="#cccccc",
                          xytext=(3, 3), textcoords="offset points")

ax_tasks.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x/1000:.0f}K" if x < 1_000_000 else f"${x/1_000_000:.1f}M"))
ax_tasks.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
ax_tasks.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
plt.setp(ax_tasks.xaxis.get_majorticklabels(), rotation=30, ha="right")
ax_tasks.set_title("Task Outcomes  (▲ success  ● fail,  size = prestige req)", color="white", fontsize=10, pad=8)
ax_tasks.set_ylabel("Reward Value", color="#aaaaaa", fontsize=9)
ax_tasks.grid(axis="y", color="#333344", linewidth=0.5, linestyle="--")

# Legend patches
from matplotlib.patches import Patch
ax_tasks.legend(handles=[
    Patch(color="#2ecc71", label=f"Success ({sum(task_success)})"),
    Patch(color="#e74c3c", label=f"Fail ({sum(not s for s in task_success)})"),
], fontsize=8, facecolor="#1a1d27", edgecolor="#333344", labelcolor="white")

# ── Summary annotation ───────────────────────────────────────────────────
final_bal = balances[-1] if balances else 0
fig.text(0.5, 0.97,
         f"minimax-m2.5  |  seed=42  |  harder config  |  "
         f"150 turns  |  Aug 2025 sim time  |  "
         f"final balance ${final_bal/1_000_000:.2f}M  |  "
         f"{sum(task_success)}/{len(task_success)} tasks succeeded",
         ha="center", va="top", color="#aaaaaa", fontsize=9)

out = Path("plot_run_hard.png")
plt.savefig(out, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
print(f"Saved: {out}")
