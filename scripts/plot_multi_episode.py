"""Plot multi-episode benchmark: funds over time across episodes + scratchpad evolution."""
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import matplotlib.gridspec as gridspec
from matplotlib.patches import FancyBboxPatch
import textwrap

ROOT = Path(__file__).parent.parent
INITIAL_FUNDS_CENTS = 15_000_000

# ── Collinear brand palette ──────────────────────────────────────────────────
NAVY     = "#13234D"
ORANGE   = "#F26125"
BLUE     = "#4D65FF"
BG_COLOR = "#FAFBFD"
GRID_CLR = "#E8ECF2"
TEXT_CLR = "#2A2F3D"
MUTED    = "#6B7694"
CARD_BG  = "#FFFFFF"

EP_COLORS = [BLUE, ORANGE, "#22C55E"]
EP_LABELS = ["Episode 1", "Episode 2", "Episode 3"]


def load_episode(db_path):
    """Load funds curve, task stats, and scratchpad from an episode DB."""
    con = sqlite3.connect(str(db_path))
    rows = con.execute(
        "SELECT occurred_at, amount_cents, category FROM ledger_entries ORDER BY occurred_at ASC"
    ).fetchall()

    tasks = con.execute(
        "SELECT status, count(*) FROM tasks WHERE completed_at IS NOT NULL GROUP BY status"
    ).fetchall()
    task_stats = dict(tasks)

    scratchpad = con.execute("SELECT content FROM scratchpads LIMIT 1").fetchone()
    scratchpad_text = scratchpad[0] if scratchpad else ""

    con.close()

    if not rows:
        return None

    times, balances = [], []
    running = INITIAL_FUNDS_CENTS
    start = datetime.fromisoformat(rows[0][0]).replace(
        month=1, day=1, hour=9, minute=0, second=0, microsecond=0
    )
    times.append(start)
    balances.append(running / 100)
    for occurred_at, amount_cents, category in rows:
        running += int(amount_cents)
        t = datetime.fromisoformat(occurred_at)
        times.append(t)
        balances.append(running / 100)

    return {
        "times": times,
        "balances": balances,
        "final_balance": balances[-1],
        "task_success": task_stats.get("completed_success", 0),
        "task_fail": task_stats.get("completed_fail", 0),
        "scratchpad": scratchpad_text,
        "duration_months": (times[-1] - times[0]).days / 30.0,
        "bankrupt": balances[-1] <= 0,
    }


def make_plot(episodes, model_label, seed, config):
    fig = plt.figure(figsize=(20, 12), facecolor=BG_COLOR)
    gs = gridspec.GridSpec(2, 3, figure=fig, height_ratios=[2.2, 1],
                           hspace=0.35, wspace=0.3,
                           left=0.07, right=0.97, top=0.82, bottom=0.06)

    # ── Header band ──────────────────────────────────────────────────────
    header_rect = plt.Rectangle((0, 0.88), 1, 0.12,
                                transform=fig.transFigure, facecolor=NAVY,
                                edgecolor="none", zorder=0)
    fig.patches.append(header_rect)
    accent_rect = plt.Rectangle((0, 0.875), 1, 0.006,
                                transform=fig.transFigure, facecolor=ORANGE,
                                edgecolor="none", zorder=1)
    fig.patches.append(accent_rect)

    fig.text(0.5, 0.94,
             "YC-Bench  |  Multi-Episode Learning",
             ha="center", va="center",
             fontsize=32, fontweight="700", color="white",
             fontfamily="Helvetica Neue", zorder=2)
    fig.text(0.5, 0.895,
             f"{model_label}  |  {config} config  |  seed {seed}  |  {len(episodes)} episodes",
             ha="center", va="center",
             fontsize=16, fontweight="400", color="#AABBDD", zorder=2)

    # ── Top row: funds over time (full width) ────────────────────────────
    ax_funds = fig.add_subplot(gs[0, :])
    ax_funds.set_facecolor(CARD_BG)
    for spine in ax_funds.spines.values():
        spine.set_edgecolor(GRID_CLR)
        spine.set_linewidth(1.2)

    for i, ep in enumerate(episodes):
        color = EP_COLORS[i % len(EP_COLORS)]
        survived = f"{ep['duration_months']:.0f}mo"
        label = f"Ep {i+1}: {survived}, {ep['task_success']}W/{ep['task_fail']}L"

        ax_funds.plot(ep["times"], ep["balances"],
                      color=color, linewidth=2.8, alpha=0.9,
                      label=label, zorder=3 + i)
        ax_funds.fill_between(ep["times"], 0, ep["balances"],
                              color=color, alpha=0.06, zorder=1)

        if ep["bankrupt"]:
            ax_funds.scatter([ep["times"][-1]], [max(ep["balances"][-1], 500)],
                             color=color, marker="X", s=200,
                             linewidths=2, edgecolors="white",
                             alpha=0.9, zorder=5 + i)

    ax_funds.axhline(0, color="#DC2626", linewidth=1.2, linestyle="--", alpha=0.5, zorder=2,
                     label="Bankruptcy line")
    ax_funds.set_ylabel("Company Funds ($)", fontsize=14, color=TEXT_CLR, fontweight="500")
    ax_funds.yaxis.set_major_formatter(
        mticker.FuncFormatter(
            lambda x, _: f"${x/1e6:.1f}M" if x >= 1e6
            else f"${x/1e3:.0f}K" if x >= 1e3
            else f"${x:.0f}"
        )
    )
    ax_funds.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    ax_funds.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    plt.setp(ax_funds.xaxis.get_majorticklabels(), rotation=30, ha="right")
    ax_funds.tick_params(colors=MUTED, labelsize=12)
    ax_funds.grid(axis="y", color=GRID_CLR, linewidth=0.7, alpha=0.8)
    ax_funds.grid(axis="x", color=GRID_CLR, linewidth=0.4, alpha=0.4)
    ax_funds.legend(fontsize=12, facecolor=CARD_BG, edgecolor=GRID_CLR,
                    labelcolor=TEXT_CLR, loc="upper right",
                    framealpha=0.95, borderpad=1)
    ax_funds.set_title("Funds Over Time — Each Episode Starts Fresh",
                       fontsize=16, fontweight="600", color=TEXT_CLR, pad=12)

    # ── Bottom row: 3 scratchpad panels ──────────────────────────────────
    for i, ep in enumerate(episodes):
        ax = fig.add_subplot(gs[1, i])
        ax.set_facecolor("#F8F9FC")
        for spine in ax.spines.values():
            spine.set_edgecolor(EP_COLORS[i % len(EP_COLORS)])
            spine.set_linewidth(2)

        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_xticks([])
        ax.set_yticks([])

        # Title
        color = EP_COLORS[i % len(EP_COLORS)]
        ax.set_title(f"Episode {i+1} Scratchpad",
                     fontsize=13, fontweight="600", color=color, pad=8)

        # Scratchpad content (truncated)
        text = ep["scratchpad"].strip()
        if not text:
            text = "(empty)"
        # Take first ~8 lines, wrap to ~55 chars
        lines = text.split("\n")[:10]
        wrapped = []
        for line in lines:
            if len(line) > 60:
                wrapped.extend(textwrap.wrap(line, 58))
            else:
                wrapped.append(line)
        display = "\n".join(wrapped[:12])
        if len(wrapped) > 12 or len(lines) < text.count("\n") + 1:
            display += "\n..."

        ax.text(0.05, 0.92, display,
                transform=ax.transAxes,
                fontsize=7.5, fontfamily="monospace",
                color=TEXT_CLR, verticalalignment="top",
                linespacing=1.4)

        # Stats badge
        stats = f"{ep['task_success']}W / {ep['task_fail']}L  |  {ep['duration_months']:.0f} months"
        ax.text(0.5, 0.02, stats,
                transform=ax.transAxes, ha="center",
                fontsize=9, fontweight="600", color=MUTED)

    # ── Footer ───────────────────────────────────────────────────────────
    fig.text(0.5, 0.01,
             "collinear.ai  |  Multi-Episode YC-Bench: Scratchpad carries over between bankruptcies",
             ha="center", va="bottom",
             fontsize=12, fontweight="400", color=MUTED, fontstyle="italic")

    out = ROOT / "plots" / "multi_episode_haiku.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out, dpi=150, facecolor=BG_COLOR, pad_inches=0)
    print(f"Saved: {out}")


if __name__ == "__main__":
    db_dir = ROOT / "db"
    slug = "openrouter_anthropic_claude-haiku-4-5"
    config = "hard"
    seed = 1

    episodes = []
    for ep_num in [1, 2, 3]:
        db_path = db_dir / f"{config}_{seed}_{slug}.ep{ep_num}.db"
        if not db_path.exists():
            print(f"Skipping {db_path} (not found)")
            continue
        data = load_episode(db_path)
        if data:
            episodes.append(data)
            print(f"Episode {ep_num}: {data['task_success']}W/{data['task_fail']}L, "
                  f"survived {data['duration_months']:.1f}mo, "
                  f"final ${data['final_balance']:,.0f}")

    if episodes:
        make_plot(episodes, "Claude Haiku 4.5", seed, config)
    else:
        print("No episode data found.")
