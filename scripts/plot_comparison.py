"""YC-Bench comparison plot — Collinear AI branding."""
import sqlite3
from pathlib import Path
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import numpy as np

ROOT = Path(__file__).parent.parent
INITIAL_FUNDS_CENTS = 25_000_000

# ── Collinear brand palette ──────────────────────────────────────────────────
NAVY     = "#13234D"
ORANGE   = "#F26125"
BLUE     = "#4D65FF"
BG_COLOR = "#FAFBFD"
GRID_CLR = "#E8ECF2"
TEXT_CLR = "#2A2F3D"
MUTED    = "#6B7694"
CARD_BG  = "#FFFFFF"

MODELS = {
    "sonnet": {
        "slug": "anthropic_claude-sonnet-4-6",
        "label": "Sonnet 4.6",
        "color": BLUE,
    },
    "gemini": {
        "slug": "gemini_gemini-3-flash-preview",
        "label": "Gemini 3 Flash",
        "color": ORANGE,
    },
    "gpt52": {
        "slug": "openai_gpt-5.2",
        "label": "GPT-5.2",
        "color": "#22C55E",
    },
    "greedy": {
        "slug": "greedy_bot",
        "label": "Greedy Bot",
        "color": NAVY,
    },
}

BOT_KEYS = {"greedy"}

CONFIGS = ["medium", "hard", "nightmare"]
SEEDS = [1, 2, 3]

DIFF_COLORS = {"medium": BLUE, "hard": ORANGE, "nightmare": "#DC2626"}


def load_logo_image(height_px=80):
    """Render the wordmark SVG to a high-res RGBA PIL image."""
    import os, ctypes.util
    # Ensure homebrew cairo is findable
    if ctypes.util.find_library("cairo") is None:
        brew_lib = "/opt/homebrew/lib"
        if Path(brew_lib).exists():
            os.environ.setdefault("DYLD_LIBRARY_PATH", brew_lib)
    import cairosvg
    from PIL import Image
    import io
    p = ROOT / "plots" / "collinear_wordmark.svg"
    if not p.exists():
        return None
    png_data = cairosvg.svg2png(url=str(p), output_height=height_px)
    return Image.open(io.BytesIO(png_data)).convert("RGBA")


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
                    "config": config, "seed": seed,
                    "model_key": key, "label": model["label"],
                    "color": model["color"],
                    "times": times, "balances": balances,
                    "bankrupt": bankrupt,
                    "final": balances[-1] if balances else 0,
                })
                tag = "BANKRUPT" if bankrupt else f"${balances[-1]:,.0f}"
                print(f"  {config} seed={seed} {model['label']}: {tag}")
    return runs


def make_plot(runs):
    fig, axes = plt.subplots(3, 3, figsize=(30, 22), facecolor=BG_COLOR)

    # ── Header band (drawn as a filled Rectangle patch on the figure) ────
    from matplotlib.patches import FancyBboxPatch
    header_rect = plt.Rectangle((0, 0.90), 1, 0.10,
                                transform=fig.transFigure, facecolor=NAVY,
                                edgecolor="none", zorder=0)
    fig.patches.append(header_rect)
    # Orange accent line under header
    accent_rect = plt.Rectangle((0, 0.895), 1, 0.006,
                                transform=fig.transFigure, facecolor=ORANGE,
                                edgecolor="none", zorder=1)
    fig.patches.append(accent_rect)

    fig.text(
        0.5, 0.955,
        "YC-Bench  |  1-Year Horizon",
        ha="center", va="center",
        fontsize=50, fontweight="700", color="white",
        fontfamily="Helvetica Neue", zorder=2,
    )
    # ── Common legend in header ─────────────────────────────────────────
    legend_items = [
        ("Sonnet 4.6", BLUE, "-", 4.0, 0.95),
        ("Gemini 3 Flash", ORANGE, "-", 4.0, 0.95),
        ("GPT-5.2", "#22C55E", "-", 4.0, 0.95),
        ("Greedy Bot", NAVY, "--", 3.5, 0.75),
    ]
    legend_handles = []
    for lbl, clr, ls, lw, alpha in legend_items:
        line = plt.Line2D([0], [0], color=clr, linewidth=lw, linestyle=ls,
                          alpha=alpha)
        legend_handles.append(line)
    legend_labels = [item[0] for item in legend_items]
    fig.legend(
        legend_handles, legend_labels,
        loc="center", bbox_to_anchor=(0.53, 0.855),
        ncol=4, fontsize=22, frameon=False,
        labelcolor=TEXT_CLR, handlelength=3.5, handletextpad=1.0,
        columnspacing=3.0,
    )

    # Pre-render logo from SVG at high res (will composite after savefig)
    logo_img = load_logo_image(height_px=120)

    for row, config in enumerate(CONFIGS):
        for col, seed in enumerate(SEEDS):
            ax = axes[row][col]
            ax.set_facecolor(CARD_BG)

            for spine in ax.spines.values():
                spine.set_edgecolor(GRID_CLR)
                spine.set_linewidth(1.2)

            # Log scale on y-axis
            ax.set_yscale("log")

            # Reference lines
            ax.axhline(250_000, color=MUTED, linewidth=0.8, linestyle=":", alpha=0.3, zorder=1)

            cell_runs = [r for r in runs if r["config"] == config and r["seed"] == seed]

            # Sort: bots first (background), then survivors desc, then bankrupt
            def sort_key(r):
                if r["model_key"] in BOT_KEYS: return (0, 0)
                if not r["bankrupt"]: return (1, -r["final"])
                return (2, 0)
            cell_runs.sort(key=sort_key)

            for r in cell_runs:
                if not r["times"]:
                    continue
                is_bot = r["model_key"] in BOT_KEYS

                if r["bankrupt"]:
                    alpha, lw, ls = 0.4, 2.0, "-" if not is_bot else "--"
                elif is_bot:
                    alpha, lw, ls = 0.75, 3.5, "--"
                else:
                    alpha, lw, ls = 0.95, 3.0, "-"

                val = r["final"]
                if r["bankrupt"]:
                    lbl = f"{r['label']} — bankrupt"
                elif val >= 1e6:
                    lbl = f"{r['label']} — ${val/1e6:.1f}M"
                else:
                    lbl = f"{r['label']} — ${val/1e3:.0f}K"

                # Clamp balances for log scale (floor at $1K)
                plot_bals = [max(b, 1_000) for b in r["balances"]]

                ax.plot(
                    r["times"], plot_bals,
                    color=r["color"], linewidth=lw, alpha=alpha,
                    label=lbl, linestyle=ls,
                    zorder=2 if is_bot else 3,
                )

                if r["bankrupt"]:
                    ax.scatter(
                        [r["times"][-1]], [max(r["balances"][-1], 1_000)],
                        color=r["color"], marker="X", s=120,
                        linewidths=2, alpha=0.6, zorder=5,
                        edgecolors="white",
                    )
                elif not is_bot:
                    ax.scatter(
                        [r["times"][-1]], [r["balances"][-1]],
                        color=r["color"], marker="o", s=100, zorder=5,
                        edgecolors="white", linewidths=2.5,
                    )

            # No per-axis column title (seed labels placed via fig.text below)

            # Row label
            if col == 0:
                ax.set_ylabel("Funds ($)", fontsize=20, color=MUTED, fontweight="400", labelpad=10)
                ax.annotate(
                    config.upper(),
                    xy=(-0.22, 0.5), xycoords="axes fraction",
                    fontsize=23, fontweight="800",
                    color=DIFF_COLORS[config],
                    ha="center", va="center", rotation=90,
                )

            # Axes formatting
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
            ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
            ax.tick_params(colors=MUTED, labelsize=18, length=5, width=0.8, pad=6)
            ax.grid(axis="y", color=GRID_CLR, linewidth=0.7, alpha=0.8)
            ax.grid(axis="x", color=GRID_CLR, linewidth=0.4, alpha=0.4)

            ax.yaxis.set_major_formatter(
                mticker.FuncFormatter(
                    lambda x, _: f"${x/1e6:.0f}M" if x >= 1e6
                    else f"${x/1e3:.0f}K" if x >= 1e3
                    else f"${x:.0f}"
                )
            )
            ax.yaxis.set_minor_formatter(mticker.NullFormatter())

            # No per-cell legend (common legend in header)

    plt.subplots_adjust(
        left=0.08, right=0.98, top=0.79, bottom=0.05,
        hspace=0.30, wspace=0.22,
    )

    # Seed column headers just above the plot grid
    col_centers = [0.08 + (0.98 - 0.08) * (i + 0.5) / 3 for i in range(3)]
    for i, seed in enumerate(SEEDS):
        fig.text(
            col_centers[i], 0.80,
            f"Seed {seed}",
            ha="center", va="bottom",
            fontsize=26, fontweight="600", color=TEXT_CLR,
        )

    # Footer
    fig.text(
        0.5, 0.01,
        "collinear.ai  |  YC-Bench: Long-Horizon Deterministic Benchmark for LLM Agents",
        ha="center", va="bottom",
        fontsize=18, fontweight="400", color=MUTED,
        fontstyle="italic",
    )

    out = ROOT / "plots" / "sonnet_vs_gemini.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    dpi = 150
    plt.savefig(out, dpi=dpi, facecolor=BG_COLOR, pad_inches=0)

    # Composite SVG logo onto the navy header band
    if logo_img is not None:
        from PIL import Image
        plot_img = Image.open(out).convert("RGBA")
        img_w, img_h = plot_img.size
        # Header band is top 10% of image (no pad_inches)
        header_top = 0
        header_h = int(img_h * 0.10)
        # Scale logo to ~65% of header height
        target_h = int(header_h * 0.65)
        scale = target_h / logo_img.size[1]
        logo = logo_img.resize((int(logo_img.size[0] * scale), target_h), Image.LANCZOS)
        # Center vertically in the navy header band
        y_offset = header_top + (header_h - target_h) // 2
        x_offset = 70
        plot_img.paste(logo, (x_offset, y_offset), logo)
        plot_img.save(out)

    print(f"\nSaved: {out}")


if __name__ == "__main__":
    print("Loading runs...")
    runs = load_all()
    make_plot(runs)
