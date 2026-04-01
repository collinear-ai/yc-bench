"""Plot all statistics from YC-Bench result JSON files.

Usage:
    uv run python scripts/plot_run.py results/yc_bench_result_medium_1_*.json
    uv run python scripts/plot_run.py results/some_result.json  # single run
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

COLORS = [
    "#00d4aa",
    "#ff6b6b",
    "#4ecdc4",
    "#ffe66d",
    "#a29bfe",
    "#fd79a8",
    "#6c5ce7",
    "#00b894",
]
DOMAIN_COLORS = {
    "research": "#3498db",
    "inference": "#9b59b6",
    "data_environment": "#1abc9c",
    "training": "#e67e22",
}


def load(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def short_name(data: dict, path: str) -> str:
    model = data.get("model", "")
    if "/" in model:
        return model.split("/")[-1]
    return Path(path).stem.split("_", 4)[-1]


# ---------------------------------------------------------------------------
# Individual plot functions
# ---------------------------------------------------------------------------


def plot_funds(ax, runs):
    for i, (path, data) in enumerate(runs):
        funds = data["time_series"]["funds"]
        if not funds:
            continue
        times = [dt(f["time"]) for f in funds]
        vals = [f["funds_cents"] / 100 for f in funds]
        ax.plot(
            times,
            vals,
            color=COLORS[i % len(COLORS)],
            linewidth=2,
            label=short_name(data, path),
        )
    ax.axhline(y=200000, color="gray", linestyle="--", alpha=0.3)
    ax.set_ylabel("Funds ($)")
    ax.set_title("Funds Over Time")


def plot_tasks_cumulative(ax, runs):
    for i, (path, data) in enumerate(runs):
        tasks = data["time_series"].get("tasks", [])
        ok = sorted(
            [t for t in tasks if t.get("success") is True and t.get("completed_at")],
            key=lambda t: t["completed_at"],
        )
        fail = sorted(
            [t for t in tasks if t.get("success") is False and t.get("completed_at")],
            key=lambda t: t["completed_at"],
        )
        color = COLORS[i % len(COLORS)]
        name = short_name(data, path)
        if ok:
            ax.step(
                [dt(t["completed_at"]) for t in ok],
                range(1, len(ok) + 1),
                color=color,
                linewidth=2,
                label=f"{name} OK",
                where="post",
            )
        if fail:
            ax.step(
                [dt(t["completed_at"]) for t in fail],
                range(1, len(fail) + 1),
                color=color,
                linewidth=1.5,
                linestyle="--",
                label=f"{name} fail",
                where="post",
                alpha=0.6,
            )
    ax.set_ylabel("Cumulative Tasks")
    ax.set_title("Task Completions (OK vs Fail)")


def plot_prestige(ax, runs):
    # Only plot first run's prestige to avoid clutter
    if not runs:
        return
    path, data = runs[0]
    prestige = data["time_series"].get("prestige", [])
    if not prestige:
        return
    domains = sorted(set(p["domain"] for p in prestige))
    for domain in domains:
        pts = [p for p in prestige if p["domain"] == domain]
        times = [dt(p["time"]) for p in pts]
        levels = [p["level"] for p in pts]
        ax.plot(
            times,
            levels,
            color=DOMAIN_COLORS.get(domain, "gray"),
            linewidth=1.5,
            label=domain,
        )
    ax.set_ylabel("Prestige Level")
    ax.set_title(f"Prestige by Domain ({short_name(data, path)})")


def plot_trust(ax, runs):
    if not runs:
        return
    path, data = runs[0]
    trust = data["time_series"].get("client_trust", [])
    if not trust:
        return
    clients = sorted(set(t["client_name"] for t in trust))
    for client in clients:
        pts = [t for t in trust if t["client_name"] == client]
        times = [dt(t["time"]) for t in pts]
        levels = [t["trust_level"] for t in pts]
        is_rat = pts[0].get("loyalty", 0) < -0.3
        ax.plot(
            times,
            levels,
            linewidth=1.5,
            linestyle="--" if is_rat else "-",
            label=f"{client}{'*' if is_rat else ''}",
        )
    ax.set_ylabel("Trust Level")
    ax.set_title(f"Client Trust ({short_name(data, path)}) (* = RAT)")


def plot_payroll(ax, runs):
    for i, (path, data) in enumerate(runs):
        ledger = data["time_series"].get("ledger", [])
        payrolls = [e for e in ledger if e["category"] == "monthly_payroll"]
        if not payrolls:
            continue
        # Group by month
        monthly = {}
        for p in payrolls:
            m = p["time"][:7]
            monthly[m] = monthly.get(m, 0) + abs(p["amount_cents"])
        months = sorted(monthly.keys())
        times = [datetime.strptime(m, "%Y-%m") for m in months]
        amounts = [monthly[m] / 100 for m in months]
        ax.plot(
            times,
            amounts,
            color=COLORS[i % len(COLORS)],
            linewidth=2,
            marker="o",
            markersize=3,
            label=short_name(data, path),
        )
    ax.set_ylabel("Monthly Payroll ($)")
    ax.set_title("Payroll Growth")


def plot_assignments(ax, runs):
    for i, (path, data) in enumerate(runs):
        assignments = data["time_series"].get("assignments", [])
        completed = [a for a in assignments if a.get("completed_at")]
        if not completed:
            continue
        times = [dt(a["completed_at"]) for a in completed]
        counts = [a["num_assigned"] for a in completed]
        ax.scatter(
            times,
            counts,
            color=COLORS[i % len(COLORS)],
            alpha=0.5,
            s=15,
            label=short_name(data, path),
        )
    ax.axhline(y=4, color="green", linestyle="--", alpha=0.3, label="efficient (4)")
    ax.set_ylabel("Employees Assigned")
    ax.set_title("Assignment Pattern Per Task")


def plot_tokens(ax, runs):
    for i, (path, data) in enumerate(runs):
        transcript = data.get("transcript", [])
        if not transcript or not transcript[0].get("prompt_tokens"):
            continue
        turns = [t["turn"] for t in transcript]
        prompt = [t.get("prompt_tokens", 0) for t in transcript]
        color = COLORS[i % len(COLORS)]
        ax.plot(
            turns,
            prompt,
            color=color,
            linewidth=1,
            alpha=0.7,
            label=f"{short_name(data, path)} prompt",
        )
    ax.set_ylabel("Tokens")
    ax.set_title("Prompt Tokens Per Turn")
    ax.set_xlabel("Turn")


def plot_cost(ax, runs):
    for i, (path, data) in enumerate(runs):
        transcript = data.get("transcript", [])
        if not transcript:
            continue
        costs = [t.get("cost_usd", 0) for t in transcript]
        cumulative = []
        running = 0
        for c in costs:
            running += c
            cumulative.append(running)
        turns = [t["turn"] for t in transcript]
        ax.plot(
            turns,
            cumulative,
            color=COLORS[i % len(COLORS)],
            linewidth=2,
            label=short_name(data, path),
        )
    ax.set_ylabel("Cumulative Cost ($)")
    ax.set_title("API Cost")
    ax.set_xlabel("Turn")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def print_summary(data, path):
    ts = data["time_series"]
    ledger = ts.get("ledger", [])
    cats = {}
    for e in ledger:
        cats[e["category"]] = cats.get(e["category"], 0) + e["amount_cents"]

    revenue = cats.get("task_reward", 0)
    payroll = abs(cats.get("monthly_payroll", 0))
    tasks = ts.get("tasks", [])
    ok = sum(1 for t in tasks if t.get("success") is True)
    fail = sum(1 for t in tasks if t.get("success") is False)
    gated = sum(
        1 for t in tasks if t.get("success") is True and t.get("required_trust", 0) > 0
    )

    assignments = ts.get("assignments", [])
    avg_emp = (
        sum(a["num_assigned"] for a in assignments) / len(assignments)
        if assignments
        else 0
    )

    employees = ts.get("employees", [])
    final_payroll = sum(e["salary_cents"] for e in employees) / 100 if employees else 0

    clients = ts.get("clients", [])
    rats = [c for c in clients if c.get("is_rat")]

    transcript = data.get("transcript", [])
    total_prompt = sum(t.get("prompt_tokens", 0) for t in transcript)
    total_completion = sum(t.get("completion_tokens", 0) for t in transcript)
    final_funds = (200000 * 100 + revenue - payroll) / 100

    print(f"\n{'='*60}")
    print(f"  {short_name(data, path)}")
    print(f"{'='*60}")
    print(f"  Model:     {data.get('model', '?')}")
    print(f"  Seed:      {data.get('seed', '?')}")
    print(
        f"  Terminal:  {data.get('terminal_reason', '?')} at turn {data.get('turns_completed', '?')}"
    )
    print(f"  Final:     ${final_funds:,.0f}")
    print(f"  Revenue:   ${revenue/100:,.0f} | Payroll: ${payroll/100:,.0f}")
    print(f"  Tasks:     {ok} OK, {fail} fail ({gated} trust-gated)")
    print(f"  Avg emp:   {avg_emp:.1f} per task")
    print(f"  Payroll:   ${final_payroll:,.0f}/mo (final)")
    print(
        f"  RATs:      {len(rats)} — {', '.join(c['name'] for c in rats) if rats else 'none'}"
    )
    print(f"  Scratchpad: {'yes' if ts.get('scratchpad') else 'no'}")
    total_tokens = total_prompt + total_completion
    print(
        f"  Tokens:    {total_prompt:,} prompt + {total_completion:,} completion = {total_tokens:,} total"
    )
    print(f"  Cost:      ${data.get('total_cost_usd', 0):.2f}")
    started = data.get("started_at", "")
    ended = data.get("ended_at", "")
    if started and ended:
        try:
            t0 = datetime.fromisoformat(started)
            t1 = datetime.fromisoformat(ended)
            duration = t1 - t0
            mins = duration.total_seconds() / 60
            print(f"  Time:      {started[:19]} → {ended[:19]} ({mins:.1f} min)")
        except Exception:
            print(f"  Time:      {started[:19]} → {ended[:19]}")
    else:
        print(f"  Time:      N/A")

    config = ts.get("config", {})
    if config:
        print(
            f"  Config:    salary_bump={config.get('salary_bump_pct')}, "
            f"trust_build={config.get('trust_build_rate')}, "
            f"rat_fraction={config.get('loyalty_rat_fraction')}, "
            f"fail_penalty={config.get('penalty_fail_funds_pct')}"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    if len(sys.argv) < 2:
        print("Usage: uv run python scripts/plot_run.py results/*.json")
        sys.exit(1)

    paths = sys.argv[1:]
    runs = [(p, load(p)) for p in paths]

    for path, data in runs:
        print_summary(data, path)

    fig, axes = plt.subplots(4, 2, figsize=(16, 20))
    fig.suptitle(f"YC-Bench — {len(runs)} run(s)", fontsize=14, fontweight="bold")

    plot_funds(axes[0, 0], runs)
    plot_tasks_cumulative(axes[0, 1], runs)
    plot_prestige(axes[1, 0], runs)
    plot_trust(axes[1, 1], runs)
    plot_payroll(axes[2, 0], runs)
    plot_assignments(axes[2, 1], runs)
    plot_tokens(axes[3, 0], runs)
    plot_cost(axes[3, 1], runs)

    for ax in axes.flat:
        ax.legend(fontsize=7, loc="best")
        ax.grid(True, alpha=0.2)
        ax.tick_params(labelsize=8)
        if ax.get_xlabel() != "Turn":
            try:
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
                ax.xaxis.set_major_locator(mdates.MonthLocator())
            except Exception:
                pass

    plt.tight_layout()

    Path("plots").mkdir(exist_ok=True)
    out = "plots/run_analysis.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    print(f"\nPlot saved to {out}")


if __name__ == "__main__":
    main()
