"""Plot Sonnet 4.6 results across configs and seeds — clean white style."""
import sqlite3
from pathlib import Path
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker

ROOT = Path(__file__).parent.parent
INITIAL_FUNDS_CENTS = 25_000_000

CONFIGS = [
    {"name": "medium",    "color": "#2563eb", "seeds": [1, 2, 3]},
    {"name": "hard",      "color": "#dc2626", "seeds": [1, 2, 3]},
    {"name": "nightmare", "color": "#7c3aed", "seeds": [1, 2, 3]},
]

MODEL_SLUG = "anthropic_claude-sonnet-4-6"


def load_funds_curve(db_path):
    con = sqlite3.connect(str(db_path))
    rows = con.execute(
        "SELECT occurred_at, amount_cents FROM ledger_entries ORDER BY occurred_at ASC"
    ).fetchall()
    con.close()
    if not rows:
        return [], []

    times, balances = [], []
    running = INITIAL_FUNDS_CENTS
    start = datetime.fromisoformat(rows[0][0]).replace(
        month=1, day=1, hour=9, minute=0, second=0, microsecond=0
    )
    times.append(start)
    balances.append(running / 100)

    for occurred_at, amount_cents in rows:
        running += int(amount_cents)
        times.append(datetime.fromisoformat(occurred_at))
        balances.append(running / 100)

    return times, balances


def load_all_runs():
    runs = []
    for cfg in CONFIGS:
        for seed in cfg["seeds"]:
            db_path = ROOT / "db" / f"{cfg['name']}_{seed}_{MODEL_SLUG}.db"
            if not db_path.exists():
                print(f"  Skip: {db_path.name}")
                continue
            times, balances = load_funds_curve(db_path)
            bankrupt = len(balances) > 0 and balances[-1] <= 0
            runs.append({
                "config": cfg["name"],
                "seed": seed,
                "color": cfg["color"],
                "times": times,
                "balances": balances,
                "bankrupt": bankrupt,
                "final_balance": balances[-1] if balances else 0,
                "final_time": times[-1] if times else None,
            })
            status = "BANKRUPT" if bankrupt else f"${balances[-1]:,.0f}"
            print(f"  Loaded {cfg['name']} seed={seed}: {status}")
    return runs


def make_plot(runs):
    fig, axes = plt.subplots(1, 3, figsize=(18, 6), facecolor="white", sharey=False)
    fig.suptitle(
        "Sonnet 4.6  ·  YC-Bench  ·  3 Seeds per Config  ·  1-Year Horizon",
        fontsize=15, fontweight="600", y=0.98, color="#1a1a1a",
    )

    config_names = ["medium", "hard", "nightmare"]
    config_labels = ["Medium", "Hard", "Nightmare"]

    for idx, (ax, cname, clabel) in enumerate(zip(axes, config_names, config_labels)):
        ax.set_facecolor("white")
        for spine in ax.spines.values():
            spine.set_edgecolor("#d0d0d0")
            spine.set_linewidth(0.8)

        cfg_runs = [r for r in runs if r["config"] == cname]
        color = cfg_runs[0]["color"] if cfg_runs else "#333"

        # Bankruptcy line
        ax.axhline(0, color="#ef4444", linewidth=1, linestyle="--", alpha=0.5, zorder=1)
        # Starting funds line
        ax.axhline(250_000, color="#9ca3af", linewidth=0.6, linestyle=":", alpha=0.5, zorder=1)

        survived = 0
        for r in cfg_runs:
            if not r["times"]:
                continue
            seed = r["seed"]
            alpha = 0.4 if r["bankrupt"] else 1.0
            lw = 1.2 if r["bankrupt"] else 2.2
            ls = "-"

            if r["bankrupt"]:
                label = f"Seed {seed} — bankrupt"
            else:
                label = f"Seed {seed} — ${r['final_balance']/1e6:.1f}M"
                survived += 1

            ax.plot(r["times"], r["balances"], color=color,
                    linewidth=lw, alpha=alpha, linestyle=ls, label=label, zorder=3)

            # Terminal marker
            if r["bankrupt"]:
                ax.scatter([r["times"][-1]], [r["balances"][-1]],
                           color=color, marker="x", s=60, linewidths=2, alpha=0.6, zorder=5)
            else:
                ax.scatter([r["times"][-1]], [r["balances"][-1]],
                           color=color, marker="*", s=120, zorder=5)

        # Title with survival rate
        survival_text = f"{survived}/3 survived"
        title_color = "#16a34a" if survived >= 2 else "#dc2626" if survived == 0 else "#d97706"
        ax.set_title(f"{clabel}\n", fontsize=13, fontweight="600", color="#1a1a1a", pad=12)
        ax.text(0.5, 1.01, survival_text, transform=ax.transAxes,
                fontsize=10, color=title_color, ha="center", va="bottom", fontweight="500")

        # Formatting
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        ax.tick_params(colors="#555", labelsize=8)
        ax.grid(axis="y", color="#e5e7eb", linewidth=0.5, linestyle="-")
        ax.grid(axis="x", color="#f3f4f6", linewidth=0.3, linestyle="-")

        ax.yaxis.set_major_formatter(
            mticker.FuncFormatter(
                lambda x, _: f"${x/1e6:.0f}M" if abs(x) >= 1e6
                else f"${x/1e3:.0f}K" if abs(x) >= 1e3
                else f"${x:.0f}"
            )
        )

        legend = ax.legend(fontsize=8, loc="upper left", frameon=True,
                          facecolor="white", edgecolor="#e5e7eb", framealpha=0.95)
        for text in legend.get_texts():
            text.set_color("#374151")

        if idx == 0:
            ax.set_ylabel("Company Funds", fontsize=10, color="#374151")

    plt.tight_layout(rect=[0, 0, 1, 0.94])
    out_path = ROOT / "plots" / "sonnet_results.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=180, bbox_inches="tight", facecolor="white")
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    print("Loading Sonnet 4.6 runs...")
    runs = load_all_runs()
    if not runs:
        print("No data found.")
    else:
        make_plot(runs)
