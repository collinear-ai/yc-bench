"""Pareto frontier: spend (cost or tokens) vs end funds, for the Aug 2026 sweep.

  uv run python scripts/plot_pareto.py [--out plots/aug2026_pareto.png]

A model is on the frontier when no other model reached higher mean end funds
for less spend. Only models with local rollout JSONs can appear — the other
leaderboard entries have monthly funds in data.json but no cost/token record.
"""
from __future__ import annotations

import argparse, collections, glob, json, statistics
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
from matplotlib.patches import Patch
from matplotlib.lines import Line2D

ROOT = Path(__file__).parent.parent
MONTHS = [f"2025-{m:02d}" for m in range(1, 13)]
SERIES = ["#2a78d6", "#eb6834"]
SURFACE, INK, INK2, INK3 = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8880"
FIELD, GRID = "#c9c8c2", "#e8e7e1"

KEY = {  # results-model-id -> data.json key
    "anthropic/claude-opus-5": "claude-opus-5", "x-ai/grok-4.5": "grok-4.5",
    "x-ai/grok-4.6": "grok-4.6", "qwen/qwen3.8-max": "qwen3.8-max",
    "moonshotai/kimi-k3": "kimi-k3", "openai/gpt-5.6-sol": "gpt-5.6-sol",
    "openai/gpt-5.6-terra": "gpt-5.6-terra", "openai/gpt-5.6-luna": "gpt-5.6-luna",
    "google/gemini-3.6-flash": "gemini-3.6-flash", "thinkingmachines/inkling": "inkling",
    "deepseek/deepseek-v4-pro-0813": "deepseek-v4-pro-0813",
    "meta/muse-spark-1.1": "muse-spark-1.1", "meta/muse-spark-1.2": "muse-spark-1.2",
}


def money(v, _=None):
    if abs(v) >= 1_000_000: return f"${v/1_000_000:.1f}M"
    if abs(v) >= 1_000:     return f"${v/1_000:.0f}K"
    return f"${v:,.0f}"


def frontier(points):
    """points: [(spend, funds, name)] -> subset where nothing is cheaper AND better."""
    best, out = float("-inf"), []
    for p in sorted(points, key=lambda p: p[0]):
        if p[1] > best:
            out.append(p); best = p[1]
    return out


def collect():
    agg = collections.defaultdict(lambda: {"cost": [], "tok": []})
    for f in glob.glob(str(ROOT / "results/yc_bench_result_default_*.json")):
        d = json.load(open(f))
        a = agg[d["model"].replace("openrouter/", "")]
        a["cost"].append(d.get("total_cost_usd", 0.0))
        a["tok"].append(sum(t.get("prompt_tokens", 0) + t.get("completion_tokens", 0)
                            for t in (d.get("transcript") or [])))
    data = json.loads((ROOT / "docs/static/data.json").read_text())
    import re
    names = dict(re.findall(r"^\s*'([^']+)':\s*\{ name: '([^']+)'",
                            (ROOT / "docs/index.html").read_text(), re.M))
    rows = []
    for m, a in agg.items():
        k = KEY[m]
        rows.append((statistics.mean(a["cost"]), statistics.mean(a["tok"]),
                     data[k][MONTHS[-1]], names.get(k, k)))
    return rows


def decollide(fig, texts, pad=1.5, iters=400):
    """Push overlapping labels apart vertically, in pixel space."""
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    for _ in range(iters):
        boxes = [t.get_window_extent(r).expanded(1.0, 1.0 + pad / 10) for t in texts]
        moved = False
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                if not boxes[i].overlaps(boxes[j]):
                    continue
                hi, lo = ((texts[i], texts[j]) if boxes[i].y0 >= boxes[j].y0
                          else (texts[j], texts[i]))
                for t, d in ((hi, +1.2), (lo, -1.2)):
                    dx, dy = t.get_position()
                    t.set_position((dx, dy + d))
                moved = True
        if not moved:
            return
        fig.canvas.draw()


def panel(ax, pts, xlabel, title, logx, xfmt):
    front = frontier([(x, y, n) for x, y, n in pts])
    fset = {n for *_x, n in front}
    ax.set_facecolor(SURFACE)
    if logx:
        ax.set_xscale("log")

    fx = [p[0] for p in front] + [max(p[0] for p in pts) * (1.9 if logx else 1.22)]
    fy = [p[1] for p in front] + [front[-1][1]]
    ax.step(fx, fy, where="post", color=SERIES[0], lw=1.8, alpha=0.5, zorder=2)

    ax.axhline(200_000, color=INK3, lw=1.0, ls=(0, (5, 4)), zorder=1)

    labels = []
    for x, y, n in pts:
        on = n in fset
        ax.plot([x], [y], "o", ms=11 if on else 8,
                color=SERIES[0] if on else FIELD,
                mec=SURFACE, mew=1.6, zorder=5 if on else 3)
        labels.append(ax.annotate(
            f"{n}  {money(y)}" if on else n,
            (x, y), xytext=(0, 13 if on else -17), textcoords="offset points",
            ha="center", fontsize=9.5 if on else 9,
            color=INK if on else INK2,
            fontweight="600" if on else "normal", zorder=6))

    ax.set_xlabel(xlabel, fontsize=11, color=INK2, labelpad=9)
    ax.yaxis.set_major_formatter(FuncFormatter(money))
    ax.xaxis.set_major_formatter(FuncFormatter(xfmt))
    ax.tick_params(colors=INK2, length=0, labelsize=10)
    ax.grid(color=GRID, lw=1)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.set_ylim(-180_000, 3_050_000)
    ax.set_title(title, fontsize=12.5, color=INK, fontweight="600", loc="left", pad=12)
    return front, labels


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="plots/aug2026_pareto.png")
    args = ap.parse_args()
    rows = collect()

    fig, (ax, bx) = plt.subplots(1, 2, figsize=(17, 8.6), dpi=200,
                                 gridspec_kw={"wspace": 0.16})
    fig.patch.set_facecolor(SURFACE)

    f1, l1 = panel(ax, [(c, e, n) for c, t, e, n in rows],
               "mean API cost per run (log scale)",
               "Cost frontier — what you pay", True,
               lambda v, _=None: f"${v:,.0f}" if v >= 1 else f"${v:.2f}")
    f2, l2 = panel(bx, [(t, e, n) for c, t, e, n in rows],
               "mean tokens per run (prompt + completion)",
               "Token frontier — compute, independent of price", False,
               lambda v, _=None: f"{v/1e6:.0f}M")
    ax.set_ylabel("mean end funds across 3 seeds", fontsize=11, color=INK2, labelpad=9)
    ax.annotate("$200K start", (ax.get_xlim()[1], 200_000), xytext=(-6, 7),
                textcoords="offset points", fontsize=9, color=INK3, ha="right")

    for a in (ax, bx):
        a.legend(handles=[
            Line2D([], [], marker="o", ls="", ms=9, color=SERIES[0], label="on frontier"),
            Line2D([], [], marker="o", ls="", ms=7, color=FIELD, label="dominated"),
        ], loc="lower right", frameon=False, fontsize=10, labelcolor=INK2)

    fig.suptitle("YC-Bench — spend vs outcome, Aug 2026 sweep (13 models × 3 seeds)",
                 fontsize=16, color=INK, fontweight="700", x=0.045, ha="left", y=0.975)
    fig.text(0.045, 0.932,
             "Frontier = no other model reached more funds for less spend. Caveat: a run that goes "
             "bankrupt ends early and therefore costs less, so the cheap end mixes efficiency with "
             "early failure.", fontsize=10.5, color=INK2, ha="left")

    decollide(fig, l1); decollide(fig, l2)

    out = ROOT / args.out
    out.parent.mkdir(exist_ok=True)
    fig.savefig(out, facecolor=SURFACE, bbox_inches="tight", pad_inches=0.45)
    print(f"wrote {out}")
    print("  cost frontier :", " → ".join(n for *_x, n in f1))
    print("  token frontier:", " → ".join(n for *_x, n in f2))


if __name__ == "__main__":
    raise SystemExit(main())
