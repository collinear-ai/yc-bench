"""Plot benchmark results from JSON result files.

Usage:
    uv run python scripts/plot_results.py results/yc_bench_result_hard_1_gemini*.json
    uv run python scripts/plot_results.py results/*.json --out plots/all_runs.png
    uv run python scripts/plot_results.py results/run.json --plot prestige
"""
import argparse
import json
import re
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# Regex for turn 1 format (initial prompt)
RE_INIT_TIME = re.compile(r"current_time:\s*(\S+)")
RE_INIT_FUNDS = re.compile(r"funds:\s*\$([\d,.]+)")

# Regex for subsequent turns
RE_TURN_TIME = re.compile(r"\*\*Current time\*\*:\s*(\S+)")
RE_TURN_FUNDS = re.compile(r"\*\*Funds\*\*:\s*\$[\d,.]+\s*\((\d+)\s*cents\)")

# Regex for sim resume turns
RE_NEW_SIM_TIME = re.compile(r"new_sim_time:\s*(\S+)")
RE_BALANCE_DELTA = re.compile(r"balance_delta:\s*(-?\d+)")

LINE_COLORS = ["#4fc3f7", "#2ecc71", "#e67e22", "#e74c3c", "#9b59b6", "#1abc9c", "#f1c40f", "#e91e63"]
DOMAIN_COLORS = {"research": "#4fc3f7", "inference": "#2ecc71", "data_environment": "#e67e22", "training": "#9b59b6"}

BG_COLOR = "#0f1117"
FACE_COLOR = "#1a1d27"
GRID_COLOR = "#333344"
TEXT_COLOR = "#aaaaaa"


def _style_ax(ax):
    """Apply common dark-theme styling to an axes."""
    ax.set_facecolor(FACE_COLOR)
    ax.tick_params(colors=TEXT_COLOR, labelsize=9)
    for spine in ax.spines.values():
        spine.set_edgecolor(GRID_COLOR)


def _format_time_axis(ax):
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha="right")


def _funds_formatter():
    return plt.FuncFormatter(lambda x, _: f"${x/1000:.0f}K" if abs(x) < 1_000_000 else f"${x/1_000_000:.1f}M")


# ---------------------------------------------------------------------------
# Data extraction
# ---------------------------------------------------------------------------

def parse_funds_curve(result):
    """Extract (times, funds_dollars) — prefer time_series.funds, fall back to regex."""
    ts = result.get("time_series", {}).get("funds")
    if ts:
        times = [datetime.fromisoformat(p["time"]) for p in ts]
        funds = [p["funds_cents"] / 100 for p in ts]
        return times, funds

    return _parse_funds_curve_regex(result)


def _parse_funds_curve_regex(result):
    """Legacy: extract funds curve via regex from transcript."""
    times = []
    funds = []
    running_cents = None

    for entry in result.get("transcript", []):
        text = entry.get("user_input", "")

        t_match = RE_TURN_TIME.search(text) or RE_INIT_TIME.search(text)
        f_match = RE_TURN_FUNDS.search(text)

        if f_match and t_match:
            running_cents = int(f_match.group(1))
            times.append(datetime.fromisoformat(t_match.group(1)))
            funds.append(running_cents / 100)
            continue

        if not f_match:
            f_init = RE_INIT_FUNDS.search(text)
            if f_init and t_match:
                dollar_str = f_init.group(1).replace(",", "")
                running_cents = int(float(dollar_str) * 100)
                times.append(datetime.fromisoformat(t_match.group(1)))
                funds.append(running_cents / 100)
                continue

        sim_match = RE_NEW_SIM_TIME.search(text)
        delta_match = RE_BALANCE_DELTA.search(text)
        if sim_match and running_cents is not None:
            new_time = datetime.fromisoformat(sim_match.group(1))
            delta = int(delta_match.group(1)) if delta_match else 0
            running_cents += delta
            times.append(new_time)
            funds.append(running_cents / 100)

    terminal = result.get("terminal_reason", "")
    if "bankrupt" in str(terminal) and funds and funds[-1] > 0:
        times.append(times[-1])
        funds.append(0)

    return times, funds


def parse_prestige_curves(result):
    """Extract per-domain prestige curves from time_series.prestige.

    Returns dict[domain] -> (times, levels).
    """
    ts = result.get("time_series", {}).get("prestige", [])
    if not ts:
        return {}

    by_domain = {}
    for p in ts:
        domain = p["domain"]
        by_domain.setdefault(domain, ([], []))
        by_domain[domain][0].append(datetime.fromisoformat(p["time"]))
        by_domain[domain][1].append(p["level"])

    return by_domain


# ---------------------------------------------------------------------------
# Plot functions
# ---------------------------------------------------------------------------

def make_label(result):
    model = result.get("model", "unknown")
    short = model.split("/")[-1]
    seed = result.get("seed", "?")
    return f"{short} (seed {seed})"


def plot_funds(ax, results_data):
    """Plot net worth over time on the given axes."""
    ax.axhline(0, color="#e74c3c", linewidth=0.9, linestyle="--", alpha=0.4)

    for i, (fpath, result) in enumerate(results_data):
        times, funds = parse_funds_curve(result)
        if not times:
            print(f"No funds data in {fpath}")
            continue

        color = LINE_COLORS[i % len(LINE_COLORS)]
        label = make_label(result)
        ax.plot(times, funds, color=color, linewidth=2, alpha=0.95, label=label)

        terminal = result.get("terminal_reason", "")
        marker = "x" if "bankrupt" in str(terminal) else "*"
        ax.scatter([times[-1]], [funds[-1]], color=color, s=80, marker=marker, linewidths=2, zorder=5)

        if terminal:
            ax.annotate(
                terminal,
                (times[-1], funds[-1]),
                textcoords="offset points",
                xytext=(8, -5),
                fontsize=7,
                color=color,
                alpha=0.8,
            )

    ax.yaxis.set_major_formatter(_funds_formatter())
    _format_time_axis(ax)
    ax.set_title("Net Worth Over Time", color="white", fontsize=12, pad=10)
    ax.set_ylabel("Funds (USD)", color=TEXT_COLOR, fontsize=9)
    ax.grid(axis="y", color=GRID_COLOR, linewidth=0.5, linestyle="--")
    ax.text(0.005, 0.03, "← bankruptcy", transform=ax.transAxes,
            color="#e74c3c", fontsize=7.5, alpha=0.6)

    if len(results_data) > 1:
        ax.legend(fontsize=8, facecolor=FACE_COLOR, edgecolor=GRID_COLOR,
                  labelcolor="white", loc="best")


def plot_prestige(ax, results_data):
    """Plot prestige per domain over time."""
    for i, (fpath, result) in enumerate(results_data):
        curves = parse_prestige_curves(result)
        if not curves:
            print(f"No prestige data in {fpath}")
            continue

        label_prefix = make_label(result) + " " if len(results_data) > 1 else ""
        for domain, (times, levels) in sorted(curves.items()):
            color = DOMAIN_COLORS.get(domain, LINE_COLORS[i % len(LINE_COLORS)])
            ax.plot(times, levels, color=color, linewidth=2, alpha=0.9,
                    label=f"{label_prefix}{domain}", marker="o", markersize=4)

    _format_time_axis(ax)
    ax.set_title("Prestige Over Time", color="white", fontsize=12, pad=10)
    ax.set_ylabel("Prestige Level", color=TEXT_COLOR, fontsize=9)
    ax.grid(axis="y", color=GRID_COLOR, linewidth=0.5, linestyle="--")
    ax.legend(fontsize=8, facecolor=FACE_COLOR, edgecolor=GRID_COLOR,
              labelcolor="white", loc="best")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Plot benchmark results from JSON files")
    p.add_argument("files", nargs="+", help="JSON result file paths")
    p.add_argument("--out", default=None, help="Output PNG path (default: auto-generated in plots/)")
    p.add_argument("--plot", default="funds", choices=["funds", "prestige"],
                   help="Plot mode (default: funds)")
    return p.parse_args()


def main():
    args = parse_args()

    # Load all result files
    results_data = []
    for fpath in args.files:
        with open(fpath) as f:
            results_data.append((fpath, json.load(f)))

    fig, ax = plt.subplots(figsize=(12, 5), facecolor=BG_COLOR)
    _style_ax(ax)

    if args.plot == "funds":
        plot_funds(ax, results_data)
    elif args.plot == "prestige":
        plot_prestige(ax, results_data)

    suffix = f"_{args.plot}" if args.plot != "funds" else ""

    plt.tight_layout()

    if args.out:
        out = Path(args.out)
    elif len(args.files) == 1:
        out = Path("plots") / f"{Path(args.files[0]).stem}{suffix}.png"
    else:
        out = Path("plots") / f"results_comparison{suffix}.png"

    out.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"Saved: {out}")


if __name__ == "__main__":
    main()
