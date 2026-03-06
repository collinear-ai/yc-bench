"""Multi-model comparison plot: funds curves + cost vs budget.

Run from the repo root:
    uv run python scripts/plot_multi_model.py [--seed 1] [--config hard] [--budget 30]
"""
import argparse
import json
import sqlite3
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime

ROOT = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Default runs — edit or pass via CLI
# ---------------------------------------------------------------------------

DEFAULT_RUNS = [
    {"label": "gemini-flash",  "model_slug": "openrouter_google_gemini-3-flash-preview", "color": "#4fc3f7"},
    {"label": "minimax-m2.5",  "model_slug": "openrouter_minimax_minimax-m2.5",          "color": "#f39c12"},
    {"label": "kimi-k2.5",    "model_slug": "openrouter_moonshotai_kimi-k2.5",           "color": "#2ecc71"},
]

INITIAL_FUNDS_CENTS = 25_000_000  # $250K


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--config", default="hard")
    p.add_argument("--budget", type=float, default=30.0)
    p.add_argument("--out", default=None, help="Output PNG path (default: plots/funds_curves.png)")
    return p.parse_args()


def load_funds_curve(db_path: Path):
    """Reconstruct running balance from ledger entries."""
    con = sqlite3.connect(str(db_path))
    rows = con.execute(
        "SELECT occurred_at, amount_cents FROM ledger_entries ORDER BY occurred_at ASC"
    ).fetchall()
    con.close()

    if not rows:
        return [], []

    times, balances = [], []
    running = INITIAL_FUNDS_CENTS
    # Prepend the sim start (day before first ledger event, pegged to Jan 1)
    start = datetime.fromisoformat(rows[0][0]).replace(
        month=1, day=1, hour=9, minute=0, second=0, microsecond=0
    )
    times.append(start)
    balances.append(INITIAL_FUNDS_CENTS / 100)

    for occurred_at, amount_cents in rows:
        running += int(amount_cents)
        times.append(datetime.fromisoformat(occurred_at))
        balances.append(running / 100)

    return times, balances


def load_meta(result_path: Path):
    with open(result_path) as f:
        d = json.load(f)
    return {
        "turns": d.get("turns_completed", 0),
        "terminal_reason": d.get("terminal_reason", "unknown"),
        "cost_usd": d.get("total_cost_usd", 0.0),
        "horizon_years": d.get("horizon_years", 3),
    }


def load_run_data(runs, seed):
    run_data = []
    for run in runs:
        slug = run["model_slug"]
        db_path = ROOT / "db" / f"{seed}_{slug}.db"
        result_path = ROOT / "results" / f"yc_bench_result_{seed}_{slug}.json"

        if not db_path.exists() or not result_path.exists():
            print(f"  Skipping {run['label']}: missing {db_path.name} or {result_path.name}")
            continue

        times, balances = load_funds_curve(db_path)
        meta = load_meta(result_path)
        run_data.append({**run, "times": times, "balances": balances, **meta})
        print(f"  Loaded {run['label']}: {meta['turns']} turns, {meta['terminal_reason']}, ${meta['cost_usd']:.4f}")

    return run_data


# ---------------------------------------------------------------------------
# Plot
# ---------------------------------------------------------------------------

def make_plot(run_data, seed, config_name, budget_usd, out_path: Path):
    total_cost = sum(r["cost_usd"] for r in run_data)
    budget_pct = (total_cost / budget_usd) * 100 if budget_usd else 0

    fig, (ax_funds, ax_cost) = plt.subplots(
        1, 2,
        figsize=(17, 6.5),
        facecolor="#0f1117",
        gridspec_kw={"width_ratios": [3, 1]},
    )

    for ax in [ax_funds, ax_cost]:
        ax.set_facecolor("#1a1d27")
        ax.tick_params(colors="#aaaaaa", labelsize=9)
        for spine in ax.spines.values():
            spine.set_edgecolor("#333344")

    # Horizon annotation (approx end date)
    horizon_years = run_data[0]["horizon_years"] if run_data else 3
    horizon_label = f"{horizon_years}-year horizon"

    fig.suptitle(
        f"YC-Bench · {len(run_data)}-Model Comparison · seed={seed} · {config_name}  ({horizon_label}, 5 employees)\n"
        f"Total API spend: ${total_cost:.2f} / ${budget_usd:.0f} budget  ({budget_pct:.1f}%)",
        color="white", fontsize=13, y=1.02,
    )

    # ── Funds curves ─────────────────────────────────────────────────────────
    ax_funds.axhline(0, color="#e74c3c", linewidth=0.9, linestyle="--", alpha=0.4, zorder=1)
    ax_funds.axhline(250_000, color="#555577", linewidth=0.7, linestyle=":", alpha=0.6, zorder=1)

    for r in run_data:
        if not r["times"]:
            continue
        reason = r["terminal_reason"]
        turns = r["turns"]

        reason_short = {"bankruptcy": "bankrupt", "horizon_end": "survived!", "error": "error"}.get(reason, reason)
        label = f"{r['label']}  ({turns}t · {reason_short})"

        lw = 2.2 if reason == "horizon_end" else 1.8
        alpha = 1.0 if reason == "horizon_end" else 0.85
        ax_funds.plot(r["times"], r["balances"], color=r["color"],
                      linewidth=lw, alpha=alpha, label=label, zorder=3)

        # Mark terminal point
        marker = "★" if reason == "horizon_end" else "x"
        msize = 100 if reason == "horizon_end" else 70
        ax_funds.scatter([r["times"][-1]], [r["balances"][-1]],
                         color=r["color"], s=msize, zorder=5,
                         marker="*" if reason == "horizon_end" else "x", linewidths=2)

    ax_funds.yaxis.set_major_formatter(
        plt.FuncFormatter(lambda x, _: f"${x/1000:.0f}K" if abs(x) < 1_000_000 else f"${x/1_000_000:.1f}M")
    )
    ax_funds.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    ax_funds.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    plt.setp(ax_funds.xaxis.get_majorticklabels(), rotation=30, ha="right")
    ax_funds.set_title("Company Funds Over Sim Time", color="white", fontsize=12, pad=8)
    ax_funds.set_ylabel("Balance (USD)", color="#aaaaaa", fontsize=9)
    ax_funds.legend(fontsize=9, facecolor="#1a1d27", edgecolor="#333344",
                    labelcolor="white", loc="upper right")
    ax_funds.grid(axis="y", color="#333344", linewidth=0.5, linestyle="--")
    ax_funds.text(0.005, 0.03, "← bankruptcy", transform=ax_funds.transAxes,
                  color="#e74c3c", fontsize=7.5, alpha=0.6)

    # ── Cost vs budget bars ──────────────────────────────────────────────────
    labels = [r["label"] for r in run_data]
    costs  = [r["cost_usd"] for r in run_data]
    colors = [r["color"] for r in run_data]
    y_pos  = list(range(len(labels)))

    bars = ax_cost.barh(y_pos, costs, color=colors, alpha=0.85, height=0.55, zorder=3)

    # Total bar
    total_y = len(labels) + 0.6
    ax_cost.barh(total_y, total_cost, color="#f1c40f", alpha=0.45, height=0.45, zorder=3)

    # Budget line
    ax_cost.axvline(budget_usd, color="#f1c40f", linewidth=1.6, linestyle="--",
                    zorder=4, label=f"${budget_usd:.0f} budget")

    # Value labels
    for i, cost in enumerate(costs):
        ax_cost.text(cost + budget_usd * 0.01, i, f"${cost:.3f}",
                     va="center", color="#dddddd", fontsize=8)
    ax_cost.text(total_cost + budget_usd * 0.01, total_y, f"${total_cost:.2f}",
                 va="center", color="#f1c40f", fontsize=8.5, fontweight="bold")

    ax_cost.set_yticks(y_pos + [total_y])
    ax_cost.set_yticklabels(labels + ["TOTAL"], color="#aaaaaa", fontsize=8)
    ax_cost.set_xlabel("API Cost (USD)", color="#aaaaaa", fontsize=9)
    ax_cost.set_title(f"Cost vs ${budget_usd:.0f} Budget", color="white", fontsize=12, pad=8)
    ax_cost.legend(fontsize=8, facecolor="#1a1d27", edgecolor="#333344", labelcolor="white")
    ax_cost.set_xlim(0, max(budget_usd * 1.15, max(costs) * 1.4 if costs else 1))
    ax_cost.grid(axis="x", color="#333344", linewidth=0.5, linestyle="--")

    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"Saved: {out_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    args = parse_args()
    out = Path(args.out) if args.out else ROOT / "plots" / "funds_curves.png"

    print(f"Loading runs: seed={args.seed}, config={args.config}")
    run_data = load_run_data(DEFAULT_RUNS, args.seed)

    if not run_data:
        print("No data found. Run benchmarks first.")
    else:
        make_plot(run_data, args.seed, args.config, args.budget, out)
