"""Collinear-branded matplotlib summary of the policy-as-code experiment.

Two studies, four panels:
  (a) Iteration trajectories — funds vs iteration, one line per model.
  (b) First-try variance — per-trial outcomes for Opus and GPT, seed=1.
  (c) Codegen cost vs reward — scatter across all runs.
  (d) Failure-mode breakdown — stacked bar per model.

Output: imgs/policy_experiment_results.png
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch

# ---------------------------------------------------------------------------
# Collinear brand tokens (subset)
# ---------------------------------------------------------------------------
BEIGE_200 = "#F8F7F2"   # surface
BEIGE_100 = "#FBFAF6"   # cards
INK_900 = "#171616"     # primary text
INK_600 = "#4A4847"     # secondary
INK_400 = "#8C8A87"     # muted
INK_300 = "#C9C7C2"     # borders
INK_200 = "#E5E3DC"     # subtle dividers
ORANGE = "#F26125"      # accent — 15% rule
SUCCESS = "#3C8B5E"
DANGER = "#B5462C"

SANS = ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"]
MONO = ["Menlo", "Monaco", "Courier New", "DejaVu Sans Mono"]

plt.rcParams.update({
    "figure.facecolor": BEIGE_200,
    "axes.facecolor": BEIGE_100,
    "axes.edgecolor": INK_300,
    "axes.linewidth": 1.0,
    "axes.labelcolor": INK_900,
    "axes.titlecolor": INK_900,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "xtick.color": INK_600,
    "ytick.color": INK_600,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "axes.labelsize": 10,
    "axes.titlesize": 12,
    "font.family": SANS,
    "font.size": 10,
    "text.color": INK_900,
    "axes.grid": True,
    "grid.color": INK_200,
    "grid.linewidth": 0.6,
    "grid.linestyle": "-",
    "axes.axisbelow": True,
    "savefig.facecolor": BEIGE_200,
    "savefig.edgecolor": "none",
})

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------
MODELS = {
    "openrouter/anthropic/claude-opus-4.7": "Opus 4.7",
    "openrouter/google/gemini-3.1-pro-preview": "Gemini 3.1 Pro",
    "openrouter/openai/gpt-5.5": "GPT-5.5",
}
SHORT = {v: v.split()[0] for v in MODELS.values()}  # "Opus", "Gemini", "GPT-5.5"
SHORT["GPT-5.5"] = "GPT-5.5"

# Highlight one model in orange — GPT-5.5 is the protagonist here.
COLOR_FOR = {
    "Opus 4.7": INK_400,
    "Gemini 3.1 Pro": INK_600,
    "GPT-5.5": ORANGE,
}


def load_iter_history(slug: str) -> list[dict]:
    p = REPO / "results" / f"iterate_history_default_1_{slug}.json"
    if not p.exists():
        return []
    return json.loads(p.read_text())


def load_variance_summary() -> list[dict]:
    p = REPO / "results" / "variance_summary.json"
    if not p.exists():
        return []
    return json.loads(p.read_text())


def fmt_funds(c: int | None) -> str:
    if c is None:
        return "—"
    v = c / 100
    if abs(v) >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:,.0f}"


# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------
def panel_iteration(ax) -> None:
    ax.set_title("Funds across hill-climbing iterations  ·  seed 1",
                 loc="left", fontweight="bold", pad=14)

    for model_id, name in MODELS.items():
        hist = load_iter_history(model_id.replace("/", "_"))
        xs, ys = [], []
        for r in hist:
            xs.append(r["iteration"])
            # Treat codegen failures as null (no episode happened); plot as gap.
            if r.get("error") and "codegen" in str(r.get("error", "")).lower():
                ys.append(np.nan)
            else:
                ys.append(r.get("final_funds_cents", 0) / 100)
        if not xs:
            continue
        c = COLOR_FOR[name]
        is_winner = name == "GPT-5.5"
        ax.plot(
            xs, ys,
            color=c,
            linewidth=2.2 if is_winner else 1.4,
            marker="o",
            markersize=5 if is_winner else 4,
            markerfacecolor=c,
            markeredgecolor=BEIGE_100,
            markeredgewidth=1,
            label=name,
            zorder=3 if is_winner else 2,
        )
        # Annotate the winning peak (GPT iter 10).
        if is_winner and ys:
            best_i = int(np.nanargmax(ys))
            ax.annotate(
                fmt_funds(int(ys[best_i] * 100)),
                xy=(xs[best_i], ys[best_i]),
                xytext=(8, 6),
                textcoords="offset points",
                fontsize=9, color=ORANGE, fontweight="bold",
                family=MONO,
            )

    ax.axhline(200_000, color=INK_300, linewidth=1, linestyle="--", zorder=1)
    ax.text(0.5, 200_000, "  start  $200K", color=INK_400,
            fontsize=8, va="center", family=MONO)
    ax.axhline(0, color=INK_400, linewidth=0.8, zorder=1)

    ax.set_xlabel("Iteration")
    ax.set_ylabel("Final funds")
    ax.set_xticks(range(1, 11))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(
        lambda v, _: fmt_funds(int(v * 100))
    ))
    leg = ax.legend(
        loc="upper left",
        frameon=True, facecolor=BEIGE_100, edgecolor=INK_300,
        fontsize=9, framealpha=1,
    )
    leg.get_frame().set_linewidth(0.8)


def panel_variance(ax) -> None:
    ax.set_title("First-try variance  ·  10 fresh codegens per model, seed 1",
                 loc="left", fontweight="bold", pad=14)

    summary = load_variance_summary()
    by_model: dict[str, list[dict]] = {}
    for r in summary:
        m = r["model"]
        by_model.setdefault(m, []).append(r)

    # Order: Opus left, GPT right (matches study)
    order = [
        "openrouter/anthropic/claude-opus-4.7",
        "openrouter/openai/gpt-5.5",
    ]
    labels = ["Opus 4.7", "GPT-5.5"]

    for x_pos, (model_id, name) in enumerate(zip(order, labels)):
        rows = by_model.get(model_id, [])
        funds = []
        statuses = []
        for r in rows:
            if isinstance(r.get("final_funds_cents"), int):
                funds.append(r["final_funds_cents"] / 100)
                if r.get("terminal_reason") == "horizon_end":
                    statuses.append("win")
                elif r.get("bankrupt"):
                    statuses.append("bankrupt")
                else:
                    statuses.append("partial")
            else:
                # codegen failure
                funds.append(None)
                statuses.append("codegen_fail")

        # jittered scatter
        rng = np.random.default_rng(7)
        for f, s in zip(funds, statuses):
            jitter = rng.uniform(-0.16, 0.16)
            if f is None:
                ax.scatter(
                    x_pos + jitter, 0, marker="x",
                    color=INK_400, s=80, linewidths=2, zorder=3,
                )
                continue
            if s == "win":
                color, edge, sz = ORANGE, INK_900, 130
            elif s == "bankrupt":
                color, edge, sz = DANGER, INK_900, 95
            elif s == "partial":
                color, edge, sz = INK_400, INK_900, 80
            else:
                color, edge, sz = INK_300, INK_400, 70
            ax.scatter(
                x_pos + jitter, f,
                color=color, edgecolor=edge, linewidth=0.6,
                s=sz, alpha=0.95, zorder=4,
            )

    ax.axhline(200_000, color=INK_300, linewidth=1, linestyle="--", zorder=1)
    ax.axhline(0, color=INK_400, linewidth=0.8, zorder=1)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, fontsize=11, color=INK_900)
    ax.set_xlim(-0.5, len(labels) - 0.5)
    ax.set_ylabel("Final funds")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(
        lambda v, _: fmt_funds(int(v * 100))
    ))

    # Custom legend
    handles = [
        Patch(facecolor=ORANGE, edgecolor=INK_900, label="horizon_end (win)"),
        Patch(facecolor=DANGER, edgecolor=INK_900, label="bankrupt"),
        Patch(facecolor=INK_400, edgecolor=INK_900, label="partial / passive"),
    ]
    leg = ax.legend(
        handles=handles, loc="upper left",
        frameon=True, facecolor=BEIGE_100, edgecolor=INK_300,
        fontsize=9, framealpha=1,
    )
    leg.get_frame().set_linewidth(0.8)


def panel_cost_vs_reward(ax) -> None:
    ax.set_title("Codegen cost vs final funds  ·  every run pooled",
                 loc="left", fontweight="bold", pad=14)

    # Iteration runs
    for model_id, name in MODELS.items():
        hist = load_iter_history(model_id.replace("/", "_"))
        xs, ys = [], []
        for r in hist:
            cost = r.get("codegen_cost_usd")
            funds = r.get("final_funds_cents")
            if not cost or not isinstance(funds, int):
                continue
            xs.append(cost)
            ys.append(funds / 100)
        if not xs:
            continue
        c = COLOR_FOR[name]
        ax.scatter(
            xs, ys, color=c, edgecolor=INK_900, linewidth=0.5,
            s=55, alpha=0.85, label=f"{name}  ·  iter",
            marker="o", zorder=3,
        )

    # Variance runs (triangles)
    summary = load_variance_summary()
    by_model: dict[str, list[dict]] = {}
    for r in summary:
        by_model.setdefault(r["model"], []).append(r)
    for model_id, name in MODELS.items():
        if model_id not in by_model:
            continue
        rows = by_model[model_id]
        xs = [r["codegen_cost_usd"] for r in rows
              if isinstance(r.get("final_funds_cents"), int)
              and r.get("codegen_cost_usd")]
        ys = [r["final_funds_cents"] / 100 for r in rows
              if isinstance(r.get("final_funds_cents"), int)
              and r.get("codegen_cost_usd")]
        if not xs:
            continue
        c = COLOR_FOR[name]
        ax.scatter(
            xs, ys, color=c, edgecolor=INK_900, linewidth=0.5,
            s=70, alpha=0.85, label=f"{name}  ·  fresh",
            marker="^", zorder=3,
        )

    ax.axhline(200_000, color=INK_300, linewidth=1, linestyle="--", zorder=1)
    ax.axhline(0, color=INK_400, linewidth=0.8, zorder=1)
    ax.set_xlabel("Codegen cost (USD)")
    ax.set_ylabel("Final funds")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(
        lambda v, _: fmt_funds(int(v * 100))
    ))
    leg = ax.legend(
        loc="upper left", frameon=True, facecolor=BEIGE_100,
        edgecolor=INK_300, fontsize=8, framealpha=1, ncol=2,
    )
    leg.get_frame().set_linewidth(0.8)


def panel_failure_modes(ax) -> None:
    ax.set_title("Outcome breakdown  ·  pooled across all runs",
                 loc="left", fontweight="bold", pad=14)

    # Categorise every run from both studies.
    cats = ("horizon_end", "bankrupt", "passive", "codegen_failed", "crash/other")
    counts = {name: {c: 0 for c in cats} for name in MODELS.values()}

    def categorise(r: dict) -> str:
        err = (r.get("error") or "").lower()
        funds = r.get("final_funds_cents")
        if err and "codegen" in err:
            return "codegen_failed"
        if r.get("terminal_reason") == "horizon_end":
            return "horizon_end"
        if r.get("bankrupt"):
            return "bankrupt"
        if isinstance(funds, int) and funds == 20_000_000:
            return "passive"
        if err and ("syntaxerror" in err or "nameerror" in err or "typeerror" in err
                    or "crash" in err or "timeout" in err.lower() or "budget" in err):
            return "crash/other"
        # Survived but funds shifted (partial)
        if isinstance(funds, int) and funds != 20_000_000:
            return "passive"  # bucket "partial" with passive for chart simplicity
        return "crash/other"

    # Iteration runs
    for model_id, name in MODELS.items():
        for r in load_iter_history(model_id.replace("/", "_")):
            counts[name][categorise(r)] += 1

    # Variance runs
    for r in load_variance_summary():
        name = MODELS.get(r["model"])
        if name:
            counts[name][categorise(r)] += 1

    cat_colors = {
        "horizon_end": ORANGE,
        "bankrupt": DANGER,
        "passive": INK_400,
        "codegen_failed": INK_300,
        "crash/other": INK_600,
    }
    cat_labels = {
        "horizon_end": "horizon_end (win)",
        "bankrupt": "bankrupt",
        "passive": "passive / partial",
        "codegen_failed": "codegen failed",
        "crash/other": "crash / timeout",
    }

    names = list(MODELS.values())
    y_pos = np.arange(len(names))
    left = np.zeros(len(names))
    for c in cats:
        widths = np.array([counts[n][c] for n in names])
        bars = ax.barh(
            y_pos, widths, left=left,
            color=cat_colors[c], edgecolor=BEIGE_100, linewidth=1.2,
            label=cat_labels[c],
        )
        # numeric label inside if wide enough
        for i, w in enumerate(widths):
            if w >= 2:
                ax.text(
                    left[i] + w / 2, i, str(int(w)),
                    ha="center", va="center",
                    color=BEIGE_100 if c in ("horizon_end", "bankrupt", "crash/other") else INK_900,
                    fontsize=9, fontweight="bold", family=MONO,
                )
        left += widths

    ax.set_yticks(y_pos)
    ax.set_yticklabels(names, fontsize=11, color=INK_900)
    ax.invert_yaxis()
    ax.set_xlabel("Runs")
    ax.grid(axis="y", visible=False)
    leg = ax.legend(
        loc="lower right", frameon=True, facecolor=BEIGE_100,
        edgecolor=INK_300, fontsize=8, framealpha=1, ncol=2,
    )
    leg.get_frame().set_linewidth(0.8)


# ---------------------------------------------------------------------------
# Compose
# ---------------------------------------------------------------------------
def main():
    fig = plt.figure(figsize=(16, 11), facecolor=BEIGE_200)

    # Hero band
    # Eyebrow pill: orange rule + uppercase mono label
    fig.add_artist(plt.Rectangle(
        (0.045, 0.952), 0.024, 0.004,
        facecolor=ORANGE, edgecolor="none",
        transform=fig.transFigure,
    ))
    fig.text(
        0.075, 0.95,
        "POLICY-AS-CODE  ·  YC-BENCH",
        family=MONO, fontsize=10, color=ORANGE,
        fontweight="bold",
    )
    fig.text(
        0.045, 0.92,
        "Models writing programs that run a 1-year startup sim",
        family=SANS, fontsize=22, color=INK_900, fontweight="bold",
    )
    fig.text(
        0.045, 0.89,
        "Each model writes a Python policy once, optionally consults a fixed cheap LLM helper, and is scored on final funds.",
        family=SANS, fontsize=11, color=INK_600,
    )
    fig.text(
        0.045, 0.87,
        "GPT-5.5 solves it on a fresh attempt; Opus collapses into passivity after one bankruptcy; Gemini truncates mid-codegen.",
        family=SANS, fontsize=11, color=INK_600,
    )

    # Stat strip
    stats = [
        ("BEST RESULT", "$2.10M", "GPT-5.5 fresh, t6", ORANGE),
        ("BEST AFTER 10 ITERS", "$1.56M", "GPT-5.5, iter 10", INK_900),
        ("OPUS WINS", "0 / 25", "across all studies", INK_900),
        ("GEMINI CODEGEN OK", "5 / 10", "iteration loop", INK_900),
    ]
    for i, (label, value, sub, accent) in enumerate(stats):
        x0 = 0.045 + i * 0.235
        fig.add_artist(plt.Rectangle(
            (x0, 0.795), 0.005, 0.058,
            facecolor=accent, edgecolor="none",
            transform=fig.transFigure,
        ))
        fig.text(x0 + 0.012, 0.838, label, family=MONO, fontsize=8,
                 color=INK_600, fontweight="bold")
        fig.text(x0 + 0.012, 0.812, value, family=MONO, fontsize=20,
                 color=INK_900, fontweight="bold")
        fig.text(x0 + 0.012, 0.797, sub, family=SANS, fontsize=9,
                 color=INK_400)

    # 2x2 panels grid
    gs = fig.add_gridspec(
        2, 2, left=0.05, right=0.97, top=0.74, bottom=0.10,
        hspace=0.42, wspace=0.18,
    )
    panel_iteration(fig.add_subplot(gs[0, 0]))
    panel_variance(fig.add_subplot(gs[0, 1]))
    panel_cost_vs_reward(fig.add_subplot(gs[1, 0]))
    panel_failure_modes(fig.add_subplot(gs[1, 1]))

    # Footer (clearly below the panels)
    fig.add_artist(plt.Rectangle(
        (0.045, 0.038), 0.91, 0.0008,
        facecolor=INK_300, edgecolor="none",
        transform=fig.transFigure,
    ))
    fig.text(
        0.045, 0.018,
        "yc-bench  ·  collinear-ai  ·  seed=1, config=default, helper=gemini-2.5-flash-lite (unused by every policy)",
        family=MONO, fontsize=8, color=INK_400,
    )

    out = REPO / "imgs" / "policy_experiment_results.png"
    out.parent.mkdir(exist_ok=True)
    fig.savefig(out, dpi=180, bbox_inches=None)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
