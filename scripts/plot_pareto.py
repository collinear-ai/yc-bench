"""Pareto frontier: spend (cost or tokens) vs end funds, whole leaderboard.

  uv run python scripts/plot_pareto.py [--out plots/aug2026_pareto.png]

A model is on the frontier when no other model reached higher mean end funds
for less spend. Only models with local rollout JSONs can appear — the other
leaderboard entries have monthly funds in data.json but no cost/token record.
"""
from __future__ import annotations

import argparse, collections, glob, json, math, statistics
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

SKIP_FILE = ("_re-",)          # reasoning-effort sweeps: seed 1 only, not the 3-seed config
SKIP_MODEL = ("greedy_bot", "gemma-4-31b-it")   # rule-based baseline / not on the leaderboard

KEY = {  # results-model-id -> data.json key
    "anthropic/claude-opus-5": "claude-opus-5", "x-ai/grok-4.5": "grok-4.5",
    "x-ai/grok-4.6": "grok-4.6", "qwen/qwen3.8-max": "qwen3.8-max",
    "moonshotai/kimi-k3": "kimi-k3", "openai/gpt-5.6-sol": "gpt-5.6-sol",
    "openai/gpt-5.6-terra": "gpt-5.6-terra", "openai/gpt-5.6-luna": "gpt-5.6-luna",
    "google/gemini-3.6-flash": "gemini-3.6-flash", "thinkingmachines/inkling": "inkling",
    "deepseek/deepseek-v4-pro-0813": "deepseek-v4-pro-0813",
    "meta/muse-spark-1.1": "muse-spark-1.1", "meta/muse-spark-1.2": "muse-spark-1.2",
    # prior sweeps, recovered from all_results.zip
    "anthropic/claude-fable-5": "claude-fable-5", "anthropic/claude-opus-4-6": "claude-opus-4-6",
    "anthropic/claude-opus-4-7": "claude-opus-4-7", "anthropic/claude-opus-4-8": "claude-opus-4-8",
    "anthropic/claude-sonnet-4-6": "claude-sonnet-4-6", "anthropic/claude-sonnet-5": "claude-sonnet-5",
    "gemini/gemini-3-flash-preview": "gemini-3-flash-preview",
    "gemini/gemini-3.1-flash-lite-preview": "gemini-3.1-flash-lite-preview",
    "gemini/gemini-3.1-pro-preview": "gemini-3.1-pro-preview",
    "gemini/gemini-3.5-flash": "gemini-3.5-flash", "openai/gpt-5.4": "gpt-5.4",
    "openai/gpt-5.4-mini": "gpt-5.4-mini", "openai/gpt-5.4-nano": "gpt-5.4-nano",
    "openai/gpt-5.5-2026-04-23": "gpt-5.5-2026-04-23",
    "openrouter/deepseek/deepseek-v4-pro": "deepseek-v4-pro",
    "openrouter/minimax/minimax-m3": "minimax-m3",
    "openrouter/moonshotai/kimi-k2.5": "kimi-k2.5", "openrouter/moonshotai/kimi-k2.6": "kimi-k2.6",
    "openrouter/nvidia/nemotron-3-ultra-550b-a55b": "nemotron-3-ultra-550b-a55b",
    "openrouter/qwen/qwen3.5-397b-a17b": "qwen3.5-397b-a17b",
    "openrouter/qwen/qwen3.6-plus": "qwen3.6-plus", "openrouter/qwen/qwen3.7-max": "qwen3.7-max",
    "openrouter/tencent/hy3-preview": "hy3-preview",
    "openrouter/x-ai/grok-4.20-beta": "grok-4.20-beta", "openrouter/xiaomi/mimo-v2.5": "mimo-v2.5",
    "openrouter/z-ai/glm-5": "glm-5", "openrouter/z-ai/glm-5.1": "glm-5.1",
    "openrouter/z-ai/glm-5.2": "glm-5.2",
}

# Cost tracking is not uniform: LiteLLM only records response_cost when it can price
# the model, so some direct-API runs logged $0 on some or all turns. Any model whose
# priced-turn coverage is below PRICED_MIN gets its cost rebuilt from recorded tokens
# at OpenRouter list price, and is drawn hollow so the estimate is visible.
PRICED_MIN = 0.99
PRICE = {  # data.json key -> ($/Mtok prompt, $/Mtok completion), from the OpenRouter catalogue
    "claude-fable-5":     (10.0, 50.0),   # 0% of turns priced
    "claude-opus-4-8":    (5.0, 25.0),    # 0%
    "claude-sonnet-5":    (2.0, 10.0),    # 0%
    "gpt-5.5-2026-04-23": (5.0, 30.0),    # only 33% of turns priced -> recorded cost was 4.1x low
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
    agg = collections.defaultdict(lambda: {"cost": [], "tok": [], "ptok": [], "ctok": []})
    files = (glob.glob(str(ROOT / "results/yc_bench_result_default_*.json"))
             + glob.glob(str(ROOT / "all_results/*.json")))
    for f in files:
        if any(t in Path(f).name for t in SKIP_FILE):
            continue
        d = json.load(open(f))
        model = d["model"]
        if any(t in model for t in SKIP_MODEL):
            continue
        key = KEY.get(model) or KEY.get(model.replace("openrouter/", ""))
        if key is None:
            continue
        tr = d.get("transcript") or []
        pt = sum(t.get("prompt_tokens", 0) for t in tr)
        ct = sum(t.get("completion_tokens", 0) for t in tr)
        a = agg[key]
        a["cost"].append(d.get("total_cost_usd", 0.0))
        a["ptok"].append(pt); a["ctok"].append(ct); a["tok"].append(pt + ct)
        a["turns"] = a.get("turns", 0) + len(tr)
        a["priced"] = a.get("priced", 0) + sum(1 for t in tr if t.get("cost_usd", 0) > 0)
    data = json.loads((ROOT / "docs/static/data.json").read_text())
    import re
    names = dict(re.findall(r"^\s*'([^']+)':\s*\{ name: '([^']+)'",
                            (ROOT / "docs/index.html").read_text(), re.M))
    rows = []
    for k, a in agg.items():
        if not a["tok"] or max(a["tok"]) == 0:
            continue
        cost, est = statistics.mean(a["cost"]), False
        covered = a.get("priced", 0) / a["turns"] if a.get("turns") else 0
        if covered < PRICED_MIN and k in PRICE:
            pin, pout = PRICE[k]
            cost = (statistics.mean(a["ptok"]) * pin
                    + statistics.mean(a["ctok"]) * pout) / 1e6
            est = True
        elif covered < PRICED_MIN:
            print(f"  ! {k}: only {covered:.0%} of turns priced and no list price — "
                  f"cost understated")
        if cost == 0:
            continue
        rows.append((cost, statistics.mean(a["tok"]), data[k][MONTHS[-1]],
                     names.get(k, k), est))
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


LABEL_ABOVE = 1_400_000   # dominated points below this stay unlabelled


def panel(ax, pts, xlabel, title, logx, xfmt):
    front = frontier([(x, y, n) for x, y, n, _e in pts])
    fset = {n for *_x, n in front}
    ax.set_facecolor(SURFACE)
    if logx:
        ax.set_xscale("log")
        from matplotlib.ticker import FixedLocator, NullFormatter
        ax.xaxis.set_major_locator(FixedLocator([0.3, 1, 3, 10, 30, 100, 300]))
        ax.xaxis.set_minor_formatter(NullFormatter())

    fx = [p[0] for p in front] + [max(p[0] for p in pts) * (1.9 if logx else 1.22)]
    fy = [p[1] for p in front] + [front[-1][1]]
    ax.step(fx, fy, where="post", color=SERIES[0], lw=1.8, alpha=0.5, zorder=2)

    ax.axhline(200_000, color=INK3, lw=1.0, ls=(0, (5, 4)), zorder=1)

    xs = [q[0] for q in pts]
    if logx:
        lo, hi = math.log10(min(xs)), math.log10(max(xs))
        frac = lambda v: (math.log10(v) - lo) / (hi - lo)
    else:
        lo, hi = min(xs), max(xs)
        frac = lambda v: (v - lo) / (hi - lo)

    labels = []
    for x, y, n, est in pts:
        on = n in fset
        ax.plot([x], [y], "o", ms=10 if on else 7,
                color=SURFACE if est else (SERIES[0] if on else FIELD),
                mec=(SERIES[0] if on else FIELD) if est else SURFACE,
                mew=2.0 if est else 1.5, zorder=5 if on else 3)
        if not on and y < LABEL_ABOVE:
            continue
        left = frac(x) < 0.30
        labels.append(ax.annotate(
            f"{n}  {money(y)}" if on else n,
            (x, y),
            xytext=((14, 0) if left else (0, 13 if on else -17)),
            textcoords="offset points",
            ha="left" if left else "center",
            va="center" if left else "baseline",
            fontsize=9.5 if on else 8.5,
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
    ax.set_title(title, fontsize=12.5, color=INK, fontweight="600", loc="left", pad=16)
    return front, labels


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="plots/pareto_full.png")
    args = ap.parse_args()
    rows = collect()

    fig, (ax, bx) = plt.subplots(1, 2, figsize=(17, 9.4), dpi=200,
                                 gridspec_kw={"wspace": 0.16})
    fig.subplots_adjust(top=0.80, bottom=0.11, left=0.075, right=0.985)
    fig.patch.set_facecolor(SURFACE)

    f1, l1 = panel(ax, [(c, e, n, es) for c, t, e, n, es in rows],
               "mean API cost per run (log scale)",
               "Cost frontier — what you pay", True,
               lambda v, _=None: f"${v:,.0f}" if v >= 1 else f"${v:.2f}")
    f2, l2 = panel(bx, [(t, e, n, False) for c, t, e, n, es in rows],
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

    fig.suptitle("YC-Bench — spend vs outcome, 41 models × 3 seeds",
                 fontsize=16, color=INK, fontweight="700", x=0.045, ha="left", y=0.972)
    for y, line in (
        (0.936, "Frontier = no other model reached more funds for less spend. "
                "Dominated models below $1.4M are plotted but not labelled."),
        (0.909, "Hollow markers = cost rebuilt from recorded tokens at list price, for the "
                "4 models whose runs logged incomplete cost."),
        (0.882, "Cost tracks volume, not rate card: the agent resends up to 20 rounds of history "
                "each turn, so Grok 4.5's $75/run is 117M prompt tokens vs 0.4M completion."),
    ):
        fig.text(0.045, y, line, fontsize=10.5, color=INK2, ha="left")

    decollide(fig, l1); decollide(fig, l2)

    out = ROOT / args.out
    out.parent.mkdir(exist_ok=True)
    fig.savefig(out, facecolor=SURFACE, pad_inches=0.45)
    print(f"wrote {out}")
    print("  cost frontier :", " → ".join(n for *_x, n in f1))
    print("  token frontier:", " → ".join(n for *_x, n in f2))


if __name__ == "__main__":
    raise SystemExit(main())
