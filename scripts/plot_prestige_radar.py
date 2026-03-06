"""YC-Bench prestige radar chart — final prestige per domain, Collinear AI branding."""
import sqlite3
from pathlib import Path
from math import pi

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
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
        "label": "Human Devised Rule",
        "color": NAVY,
    },
}

BOT_KEYS = {"greedy"}

CONFIGS = ["medium", "hard", "nightmare"]
SEEDS = [1, 2, 3]
DIFF_COLORS = {"medium": BLUE, "hard": ORANGE, "nightmare": "#DC2626"}

DOMAINS = ["research", "inference", "data_environment", "training"]
DOMAIN_LABELS = ["RES", "INF", "DATA/ENV", "TRAIN"]


def load_logo_image(height_px=80):
    """Render the wordmark SVG to a high-res RGBA PIL image."""
    import os, ctypes.util
    if ctypes.util.find_library("cairo") is None:
        brew_lib = "/opt/homebrew/lib"
        if Path(brew_lib).exists():
            os.environ.setdefault("DYLD_LIBRARY_PATH", brew_lib)
    try:
        import cairosvg
        from PIL import Image
        import io
        p = ROOT / "plots" / "collinear_wordmark.svg"
        if not p.exists():
            return None
        png_data = cairosvg.svg2png(url=str(p), output_height=height_px)
        return Image.open(io.BytesIO(png_data)).convert("RGBA")
    except ImportError:
        return None


def load_prestige(db_path):
    """Load final prestige levels from company_prestige table."""
    con = sqlite3.connect(str(db_path))
    rows = con.execute(
        "SELECT domain, prestige_level FROM company_prestige ORDER BY domain"
    ).fetchall()
    con.close()
    if not rows:
        return None
    prestige = {row[0]: float(row[1]) for row in rows}
    # Return values in canonical domain order
    return [prestige.get(d, 1.0) for d in DOMAINS]


def load_all():
    runs = []
    for config in CONFIGS:
        for seed in SEEDS:
            for key, model in MODELS.items():
                db_path = ROOT / "db" / f"{config}_{seed}_{model['slug']}.db"
                if not db_path.exists():
                    continue
                values = load_prestige(db_path)
                if values is None:
                    continue
                # Check if bankrupt (all prestige stuck at 1.0 = never completed a task)
                all_base = all(v <= 1.01 for v in values)
                runs.append({
                    "config": config, "seed": seed,
                    "model_key": key, "label": model["label"],
                    "color": model["color"],
                    "values": values,
                    "all_base": all_base,
                    "max_prestige": max(values),
                })
                tag = "all-1.0" if all_base else f"max={max(values):.1f}"
                print(f"  {config} seed={seed} {model['label']}: {tag}")
    return runs


def make_plot(runs):
    fig = plt.figure(figsize=(30, 22), facecolor=BG_COLOR)

    # ── Header band ──────────────────────────────────────────────────────
    header_rect = plt.Rectangle((0, 0.90), 1, 0.10,
                                transform=fig.transFigure, facecolor=NAVY,
                                edgecolor="none", zorder=0)
    fig.patches.append(header_rect)
    accent_rect = plt.Rectangle((0, 0.895), 1, 0.006,
                                transform=fig.transFigure, facecolor=ORANGE,
                                edgecolor="none", zorder=1)
    fig.patches.append(accent_rect)

    fig.text(
        0.5, 0.955,
        "YC-Bench  |  Prestige Radar  |  1-Year Horizon",
        ha="center", va="center",
        fontsize=46, fontweight="700", color="white",
        fontfamily="Helvetica Neue", zorder=2,
    )

    # ── Common legend in header ──────────────────────────────────────────
    legend_items = [
        ("Sonnet 4.6", BLUE, "-", 4.0, 0.95),
        ("Gemini 3 Flash", ORANGE, "-", 4.0, 0.95),
        ("GPT-5.2", "#22C55E", "-", 4.0, 0.95),
        ("Human Devised Rule", NAVY, "--", 3.5, 0.75),
    ]
    legend_handles = []
    for lbl, clr, ls, lw, alpha in legend_items:
        line = plt.Line2D([0], [0], color=clr, linewidth=lw, linestyle=ls, alpha=alpha)
        legend_handles.append(line)
    legend_labels = [item[0] for item in legend_items]
    fig.legend(
        legend_handles, legend_labels,
        loc="center", bbox_to_anchor=(0.53, 0.855),
        ncol=4, fontsize=22, frameon=False,
        labelcolor=TEXT_CLR, handlelength=3.5, handletextpad=1.0,
        columnspacing=3.0,
    )

    logo_img = load_logo_image(height_px=120)

    # ── Radar setup ──────────────────────────────────────────────────────
    N = len(DOMAINS)
    angles = [n / float(N) * 2 * pi for n in range(N)]
    angles += angles[:1]  # close the polygon

    # Create 3x3 grid of polar subplots
    for row, config in enumerate(CONFIGS):
        for col, seed in enumerate(SEEDS):
            ax = fig.add_subplot(3, 3, row * 3 + col + 1, polar=True)
            ax.set_facecolor(CARD_BG)

            # Configure the radar grid
            ax.set_theta_offset(pi / 2)      # Start from top
            ax.set_theta_direction(-1)         # Clockwise
            ax.set_rlabel_position(0)

            # Domain labels
            ax.set_xticks(angles[:-1])
            ax.set_xticklabels(DOMAIN_LABELS, fontsize=16, color=TEXT_CLR, fontweight="500")

            # Radial grid (prestige 1-10)
            ax.set_ylim(0, 10)
            ax.set_yticks([2, 4, 6, 8, 10])
            ax.set_yticklabels(["2", "4", "6", "8", "10"], fontsize=11, color=MUTED)

            # Grid styling
            ax.spines["polar"].set_color(GRID_CLR)
            ax.grid(color=GRID_CLR, linewidth=0.8, alpha=0.8)
            ax.tick_params(axis="x", pad=14)

            # Plot each model
            cell_runs = [r for r in runs if r["config"] == config and r["seed"] == seed]

            # Sort: bots first (background), then by max prestige desc
            def sort_key(r):
                if r["model_key"] in BOT_KEYS:
                    return (0, 0)
                return (1, -r["max_prestige"])
            cell_runs.sort(key=sort_key)

            for r in cell_runs:
                values = r["values"] + r["values"][:1]  # close polygon
                is_bot = r["model_key"] in BOT_KEYS

                if r["all_base"]:
                    alpha, lw, ls = 0.3, 2.0, "-" if not is_bot else "--"
                    fill_alpha = 0.05
                elif is_bot:
                    alpha, lw, ls = 0.75, 3.0, "--"
                    fill_alpha = 0.08
                else:
                    alpha, lw, ls = 0.95, 3.0, "-"
                    fill_alpha = 0.12

                ax.plot(angles, values, color=r["color"], linewidth=lw,
                        alpha=alpha, linestyle=ls, zorder=2 if is_bot else 3)
                ax.fill(angles, values, color=r["color"], alpha=fill_alpha,
                        zorder=1 if is_bot else 2)

    # ── Layout and labels ────────────────────────────────────────────────
    plt.subplots_adjust(
        left=0.08, right=0.98, top=0.79, bottom=0.05,
        hspace=0.35, wspace=0.28,
    )

    # Row labels (config names)
    row_y_positions = [0.70, 0.42, 0.14]  # approximate centers of each row
    for row, config in enumerate(CONFIGS):
        fig.text(
            0.025, row_y_positions[row],
            config.upper(),
            fontsize=23, fontweight="800",
            color=DIFF_COLORS[config],
            ha="center", va="center", rotation=90,
        )

    # Seed column headers
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

    out = ROOT / "plots" / "prestige_radar.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    dpi = 150
    plt.savefig(out, dpi=dpi, facecolor=BG_COLOR, pad_inches=0)

    # Composite logo
    if logo_img is not None:
        from PIL import Image
        plot_img = Image.open(out).convert("RGBA")
        img_w, img_h = plot_img.size
        header_h = int(img_h * 0.10)
        target_h = int(header_h * 0.65)
        scale = target_h / logo_img.size[1]
        logo = logo_img.resize((int(logo_img.size[0] * scale), target_h), Image.LANCZOS)
        y_offset = (header_h - target_h) // 2
        x_offset = 70
        plot_img.paste(logo, (x_offset, y_offset), logo)
        plot_img.save(out)

    print(f"\nSaved: {out}")


if __name__ == "__main__":
    print("Loading prestige data...")
    runs = load_all()
    if not runs:
        print("No data found.")
    else:
        make_plot(runs)
