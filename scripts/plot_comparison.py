"""Sonnet 4.6 vs Gemini 3 Flash — apples-to-apples comparison plot."""
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

MODELS = {
    "sonnet": {
        "slug": "anthropic_claude-sonnet-4-6",
        "label": "Sonnet 4.6",
        "color": "#2563eb",
        "dash": "-",
    },
    "gemini": {
        "slug": "gemini_gemini-3-flash-preview",
        "label": "Gemini 3 Flash",
        "color": "#f97316",
        "dash": "-",
    },
}

CONFIGS = ["medium", "hard", "nightmare"]
SEEDS = [1, 2, 3]


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
        t = datetime.fromisoformat(occurred_at)
        # Cap at end of year 1 for apples-to-apples
        if t.year > 2025:
            break
        times.append(t)
        balances.append(running / 100)

    return times, balances


def load_all():
    runs = []
    for config in CONFIGS:
        for seed in SEEDS:
            for key, model in MODELS.items():
                db_path = ROOT / "db" / f"{config}_{seed}_{model['slug']}.db"
                if not db_path.exists():
                    continue
                times, balances = load_funds_curve(db_path)
                bankrupt = len(balances) > 1 and balances[-1] <= 0
                runs.append({
                    "config": config,
                    "seed": seed,
                    "model_key": key,
                    "label": model["label"],
                    "color": model["color"],
                    "times": times,
                    "balances": balances,
                    "bankrupt": bankrupt,
                    "final": balances[-1] if balances else 0,
                })
                tag = "BANKRUPT" if bankrupt else f"${balances[-1]:,.0f}"
                print(f"  {config} seed={seed} {model['label']}: {tag}")
    return runs


def make_plot(runs):
    fig, axes = plt.subplots(3, 3, figsize=(18, 14), facecolor="white")
    fig.suptitle(
        "Sonnet 4.6  vs  Gemini 3 Flash  ·  YC-Bench  ·  1-Year Horizon",
        fontsize=16, fontweight="600", y=0.98, color="#1a1a1a",
    )

    for row, config in enumerate(CONFIGS):
        for col, seed in enumerate(SEEDS):
            ax = axes[row][col]
            ax.set_facecolor("white")
            for spine in ax.spines.values():
                spine.set_edgecolor("#d0d0d0")
                spine.set_linewidth(0.7)

            # Bankruptcy line
            ax.axhline(0, color="#ef4444", linewidth=0.8, linestyle="--", alpha=0.4)
            ax.axhline(250_000, color="#9ca3af", linewidth=0.5, linestyle=":", alpha=0.4)

            cell_runs = [r for r in runs if r["config"] == config and r["seed"] == seed]

            for r in cell_runs:
                if not r["times"]:
                    continue
                alpha = 0.35 if r["bankrupt"] else 1.0
                lw = 1.0 if r["bankrupt"] else 2.0

                if r["bankrupt"]:
                    lbl = f"{r['label']} — bankrupt"
                else:
                    val = r["final"]
                    lbl = f"{r['label']} — ${val/1e6:.1f}M" if val >= 1e6 else f"{r['label']} — ${val/1e3:.0f}K"

                ax.plot(r["times"], r["balances"], color=r["color"],
                        linewidth=lw, alpha=alpha, label=lbl, zorder=3)

                if r["bankrupt"]:
                    ax.scatter([r["times"][-1]], [r["balances"][-1]],
                               color=r["color"], marker="x", s=50, linewidths=1.5, alpha=0.5, zorder=5)
                else:
                    ax.scatter([r["times"][-1]], [r["balances"][-1]],
                               color=r["color"], marker="*", s=100, zorder=5)

            # Title
            if row == 0:
                ax.set_title(f"Seed {seed}", fontsize=11, fontweight="500", color="#374151", pad=8)

            # Row label
            if col == 0:
                ax.set_ylabel(f"{config.upper()}\n\nFunds", fontsize=10, color="#374151", fontweight="600")

            # Formatting
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
            ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
            ax.tick_params(colors="#666", labelsize=7)
            ax.grid(axis="y", color="#f0f0f0", linewidth=0.5)

            ax.yaxis.set_major_formatter(
                mticker.FuncFormatter(
                    lambda x, _: f"${x/1e6:.0f}M" if abs(x) >= 1e6
                    else f"${x/1e3:.0f}K" if abs(x) >= 1e3
                    else f"${x:.0f}"
                )
            )

            legend = ax.legend(fontsize=7, loc="upper left", frameon=True,
                              facecolor="white", edgecolor="#e5e7eb", framealpha=0.9)
            for text in legend.get_texts():
                text.set_color("#374151")

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    out = ROOT / "plots" / "sonnet_vs_gemini.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out, dpi=180, bbox_inches="tight", facecolor="white")
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    print("Loading runs...")
    runs = load_all()
    make_plot(runs)
