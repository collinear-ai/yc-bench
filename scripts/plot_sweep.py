"""Leaderboard plot: new-sweep models in context with the existing field.

  uv run python scripts/plot_sweep.py [--out plots/aug2026_sweep.png]

Left  : monthly mean net worth, top 8 coloured + direct-labelled, field in grey.
Right : final net worth for all models, new sweep accented against the field.
Source: docs/static/data.json (mean across 3 seeds; bankrupt seeds clamped to $0).
"""
from __future__ import annotations

import argparse, json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

ROOT = Path(__file__).parent.parent
MONTHS = [f"2025-{m:02d}" for m in range(1, 13)]
LABELS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

# Validated categorical palette (light mode) — fixed order, never cycled.
SERIES = ["#2a78d6","#eb6834","#1baf7a","#eda100","#e87ba4","#008300","#4a3aa7","#e34948"]
SURFACE, INK, INK2, INK3 = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8880"
FIELD, GRID = "#d6d5cf", "#e8e7e1"

# The 13 models from this sweep, by data.json key.
NEW = {
    "grok-4.5","grok-4.6","qwen3.8-max","deepseek-v4-pro-0813","muse-spark-1.1",
    "muse-spark-1.2","kimi-k3","gpt-5.6-sol","gpt-5.6-terra","gpt-5.6-luna",
    "claude-opus-5","gemini-3.6-flash","inkling",
}


def display_names(html: str) -> dict[str, str]:
    import re
    out = {}
    for k, n in re.findall(r"^\s*'([^']+)':\s*\{ name: '([^']+)'", html, re.M):
        out[k] = n
    return out


def money(v, _=None):
    if abs(v) >= 1_000_000:
        return f"${v/1_000_000:.1f}M"
    if abs(v) >= 1_000:
        return f"${v/1_000:.0f}K"
    return f"${v:,.0f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="plots/aug2026_sweep.png")
    args = ap.parse_args()

    data = json.loads((ROOT / "docs/static/data.json").read_text())
    names = display_names((ROOT / "docs/index.html").read_text())
    final = {k: v[MONTHS[-1]] for k, v in data.items()}
    ranked = sorted(final, key=lambda k: -final[k])
    top8 = ranked[:8]

    fig, (ax, bx) = plt.subplots(
        1, 2, figsize=(17, 9.5), dpi=200,
        gridspec_kw={"width_ratios": [1.35, 1], "wspace": 0.32})
    fig.patch.set_facecolor(SURFACE)

    # ---------------- Left: trajectories ----------------
    ax.set_facecolor(SURFACE)
    x = range(12)
    for k in ranked:                                   # field first, behind
        if k in top8:
            continue
        ax.plot(x, [data[k][m] for m in MONTHS], color=FIELD, lw=1.0, zorder=1)
    ax.axhline(200_000, color=INK3, lw=1.0, ls=(0, (5, 4)), zorder=2)
    ax.annotate("$200K start", (11.0, 200_000), va="bottom", ha="right",
                fontsize=9, color=INK3, xytext=(0, 5),
                textcoords="offset points")

    for i, k in enumerate(top8):
        ys = [data[k][m] for m in MONTHS]
        c = SERIES[i]
        ax.plot(x, ys, color=c, lw=2.0, zorder=4,
                solid_capstyle="round", solid_joinstyle="round")
        ax.plot([11], [ys[-1]], "o", ms=5, color=c, mec=SURFACE, mew=1.6, zorder=5)

    # de-collide the right-edge direct labels, top-down, then draw a leader
    # line from each true endpoint to its (possibly nudged) label.
    GAP, prev = 118_000, None
    for i, k in enumerate(top8):
        y_true = data[k][MONTHS[-1]]
        y_lab = y_true if prev is None else min(y_true, prev - GAP)
        prev = y_lab
        c = SERIES[i]
        if abs(y_lab - y_true) > 1_000:
            ax.plot([11.04, 11.7], [y_true, y_lab], color=c, lw=0.9,
                    alpha=0.55, zorder=3)
        tag = " ▸new" if k in NEW else ""
        ax.annotate(f"{names.get(k,k)}{tag}  {money(y_true)}",
                    (11.85, y_lab), va="center", fontsize=10.5,
                    color=INK if k in NEW else INK2,
                    fontweight="600" if k in NEW else "normal")

    ax.set_xlim(-0.3, 19.5); ax.set_xticks(list(x)); ax.set_xticklabels(LABELS, fontsize=10)
    ax.yaxis.set_major_formatter(FuncFormatter(money))
    ax.tick_params(colors=INK2, length=0, labelsize=10)
    ax.grid(axis="y", color=GRID, lw=1)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.set_title("Net worth over the 1-year horizon — top 8 named, field in grey",
                 fontsize=13, color=INK, fontweight="600", loc="left", pad=14)

    # ---------------- Right: final ranking ----------------
    bx.set_facecolor(SURFACE)
    ys = range(len(ranked))
    vals = [final[k] for k in ranked]
    cols = [SERIES[0] if k in NEW else FIELD for k in ranked]
    bx.barh(list(ys), vals, color=cols, height=0.72, zorder=3)
    bx.axvline(1_977_573, color=INK3, lw=1.0, ls=(0, (5, 4)), zorder=4)
    bx.annotate("previous best (Claude Fable 5)", (1_977_573, -0.9),
                fontsize=9, color=INK3, ha="center", va="bottom")

    bx.set_ylim(len(ranked) - 0.4, -1.9)
    bx.set_yticks(list(ys))
    bx.set_yticklabels([names.get(k, k) for k in ranked], fontsize=9.5,
                       color=INK)
    for t, k in zip(bx.get_yticklabels(), ranked):
        if k in NEW:
            t.set_fontweight("600")
        else:
            t.set_color(INK2)
    for i, (k, v) in enumerate(zip(ranked, vals)):
        if k in NEW:
            bx.annotate(money(v), (v, i), xytext=(5, 0), textcoords="offset points",
                        va="center", fontsize=9, color=INK, fontweight="600")
    bx.xaxis.set_major_formatter(FuncFormatter(money))
    bx.tick_params(axis="x", colors=INK2, length=0, labelsize=10)
    bx.tick_params(axis="y", length=0)
    bx.grid(axis="x", color=GRID, lw=1)
    bx.set_axisbelow(True)
    bx.set_xlim(min(0, min(vals) * 1.1), max(vals) * 1.16)
    for s in ("top", "right", "left"):
        bx.spines[s].set_visible(False)
    bx.spines["bottom"].set_color(GRID)
    bx.set_title("Final net worth — Aug 2026 sweep vs existing leaderboard",
                 fontsize=13, color=INK, fontweight="600", loc="left", pad=14)

    from matplotlib.patches import Patch
    bx.legend(handles=[Patch(color=SERIES[0], label="Aug 2026 sweep (13 models)"),
                       Patch(color=FIELD, label="existing leaderboard")],
              loc="lower right", frameon=False, fontsize=10, labelcolor=INK2)

    fig.suptitle("YC-Bench — mean net worth across 3 seeds, `default` preset",
                 fontsize=16, color=INK, fontweight="700", x=0.045, ha="left", y=0.975)
    fig.text(0.045, 0.938,
             "39 new runs, all terminal. Bankrupt seeds contribute $0 from bankruptcy onward "
             "(existing greedy_bot convention).",
             fontsize=10.5, color=INK2, ha="left")

    out = ROOT / args.out
    out.parent.mkdir(exist_ok=True)
    fig.savefig(out, facecolor=SURFACE, bbox_inches="tight", pad_inches=0.45)
    print(f"wrote {out}  ({out.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    raise SystemExit(main())
