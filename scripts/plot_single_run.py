"""Plot funds curve for a single benchmark run.

Usage:
    uv run python scripts/plot_single_run.py db/fast_test_1_openai_gpt-5.2-2025-12-11.db
"""
import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

INITIAL_FUNDS_CENTS = 25_000_000


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("db", help="Path to the SQLite DB file")
    p.add_argument("--out", default=None, help="Output PNG path (default: plots/<db_stem>.png)")
    return p.parse_args()


def load_funds_curve(db_path):
    con = sqlite3.connect(str(db_path))
    rows = con.execute(
        "SELECT occurred_at, amount_cents FROM ledger_entries ORDER BY occurred_at ASC"
    ).fetchall()
    con.close()

    if not rows:
        return [], []

    running = INITIAL_FUNDS_CENTS
    start = datetime.fromisoformat(rows[0][0]).replace(
        month=1, day=1, hour=9, minute=0, second=0, microsecond=0
    )
    times = [start]
    balances = [running / 100]

    for occurred_at, amount_cents in rows:
        running += int(amount_cents)
        times.append(datetime.fromisoformat(occurred_at))
        balances.append(running / 100)

    return times, balances


def make_plot(times, balances, db_name, out_path):
    fig, ax = plt.subplots(figsize=(12, 5), facecolor="#0f1117")
    ax.set_facecolor("#1a1d27")
    ax.tick_params(colors="#aaaaaa", labelsize=9)
    for spine in ax.spines.values():
        spine.set_edgecolor("#333344")

    ax.axhline(0, color="#e74c3c", linewidth=0.9, linestyle="--", alpha=0.4)
    ax.axhline(250_000, color="#555577", linewidth=0.7, linestyle=":", alpha=0.6)

    ax.plot(times, balances, color="#4fc3f7", linewidth=2, alpha=0.95)
    ax.scatter([times[-1]], [balances[-1]], color="#4fc3f7", s=80,
               marker="*" if balances[-1] > 0 else "x", linewidths=2, zorder=5)

    ax.yaxis.set_major_formatter(
        plt.FuncFormatter(lambda x, _: f"${x/1000:.0f}K" if abs(x) < 1_000_000 else f"${x/1_000_000:.1f}M")
    )
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha="right")

    final = f"${balances[-1]:,.0f}"
    ax.set_title(f"{db_name}  —  final: {final}", color="white", fontsize=12, pad=10)
    ax.set_ylabel("Balance (USD)", color="#aaaaaa", fontsize=9)
    ax.grid(axis="y", color="#333344", linewidth=0.5, linestyle="--")
    ax.text(0.005, 0.03, "← bankruptcy", transform=ax.transAxes,
            color="#e74c3c", fontsize=7.5, alpha=0.6)

    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    args = parse_args()
    db_path = Path(args.db)
    out = Path(args.out) if args.out else Path("plots") / f"{db_path.stem}.png"

    times, balances = load_funds_curve(db_path)
    if not times:
        print("No ledger entries found.")
    else:
        make_plot(times, balances, db_path.stem, out)
