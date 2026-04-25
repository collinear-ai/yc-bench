"""Streamlit dashboard — visualize YC-Bench results from all_results/ JSON files.

Usage:
    uv run streamlit run scripts/watch_dashboard.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import streamlit as st
import plotly.graph_objects as go

# ---------------------------------------------------------------------------
# Theme colors
# ---------------------------------------------------------------------------
BG_COLOR = "#0e1117"
CARD_BG = "#1a1d23"
GRID_COLOR = "#2a2d35"
TEXT_COLOR = "#e0e0e0"
TEXT_MUTED = "#8b8d93"
ACCENT_GREEN = "#00d4aa"
ACCENT_RED = "#ff4b6e"
ACCENT_BLUE = "#4da6ff"
ACCENT_YELLOW = "#ffd43b"
ACCENT_PURPLE = "#b197fc"
ACCENT_ORANGE = "#ff8c42"

# ---------------------------------------------------------------------------
# Page config & CSS
# ---------------------------------------------------------------------------

st.set_page_config(page_title="YC-Bench Results", layout="wide", page_icon="📊")

st.markdown("""
<style>
    .stApp { background-color: #0e1117; }
    .metric-card {
        background: linear-gradient(135deg, #1a1d23 0%, #21252b 100%);
        border: 1px solid #2a2d35;
        border-radius: 12px;
        padding: 20px 24px;
        text-align: center;
    }
    .metric-label {
        color: #8b8d93;
        font-size: 0.8rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-bottom: 4px;
    }
    .metric-value {
        font-size: 1.8rem;
        font-weight: 700;
        color: #e0e0e0;
        line-height: 1.2;
    }
    .metric-value.green { color: #00d4aa; }
    .metric-value.red { color: #ff4b6e; }
    .section-header {
        color: #e0e0e0;
        font-size: 1.1rem;
        font-weight: 600;
        margin: 32px 0 16px 0;
        padding-bottom: 8px;
        border-bottom: 2px solid #2a2d35;
    }
    div[data-testid="stVerticalBlock"] > div { gap: 0.5rem; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Chart helpers
# ---------------------------------------------------------------------------

def _chart_layout(title="", height=400, yaxis_title="", show_legend=True, x_range=None):
    xaxis_opts = dict(
        gridcolor=GRID_COLOR, zeroline=False,
        tickfont=dict(size=10, color=TEXT_MUTED),
    )
    if x_range:
        xaxis_opts["range"] = x_range
    return dict(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, -apple-system, sans-serif", color=TEXT_COLOR, size=12),
        title=dict(text=title, font=dict(size=14, color=TEXT_COLOR), x=0, xanchor="left"),
        height=height,
        margin=dict(l=60, r=20, t=40, b=40),
        xaxis=xaxis_opts,
        yaxis=dict(
            title=yaxis_title, gridcolor=GRID_COLOR, zeroline=False,
            tickfont=dict(size=10, color=TEXT_MUTED),
            title_font=dict(size=11, color=TEXT_MUTED),
        ),
        legend=dict(
            bgcolor="rgba(0,0,0,0)", font=dict(size=10, color=TEXT_MUTED),
            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
        ) if show_legend else dict(visible=False),
        hovermode="x unified",
    )


# ---------------------------------------------------------------------------
# Model colors
# ---------------------------------------------------------------------------

MODEL_COLORS = {
    "gpt-5.4": "#4da6ff",
    "gpt-5.4-nano": "#ff8c42",
    "gpt-5.4-mini": "#ffd43b",
    "gemini-3.1-pro-preview": "#00d4aa",
    "gemini-3-flash-preview": "#b197fc",
    "gemini-3.1-flash-lite-preview": "#9775d4",
    "claude-opus-4-6": "#ff6b8a",
    "claude-sonnet-4-6": "#e599f7",
    "grok-4.20-beta": "#69db7c",
    "greedy_bot": "#ff4b6e",
    "greedy": "#ff4b6e",
    "kimi-k2.5": "#45c4b0",
    "qwen3.5-397b": "#ffa94d",
    "glm-5": "#8b8d93",
}


def _model_color(label: str) -> str:
    for key, color in MODEL_COLORS.items():
        if key in label:
            return color
    return "#8b8d93"


# ---------------------------------------------------------------------------
# Load all results from all_results/
# ---------------------------------------------------------------------------

RESULTS_DIR = Path("all_results")


LABEL_ALIASES = {
    "greedy_bot": "greedy",
}


def _normalize_model_label(model_str: str) -> str:
    """Normalize model string to a short label."""
    # Strip provider prefixes
    label = re.sub(r"^(openai|gemini|anthropic|openrouter(/[^/]+)?)/", "", model_str)
    return LABEL_ALIASES.get(label, label)


@st.cache_data(ttl=10)
def load_all_results():
    """Load all result JSONs and return structured data."""
    if not RESULTS_DIR.exists():
        return {}

    # Group by (split_type, model_label) -> list of (seed, data)
    model_runs = defaultdict(list)

    for f in sorted(RESULTS_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue

        # Determine split type and parse metadata
        if f.name.startswith("linear_"):
            split = "linear"
            # linear_glm-5_seed1.json
            parts = f.stem.split("_seed")
            label = f.stem[len("linear_"):].rsplit("_seed", 1)[0]
            seed = int(parts[-1]) if len(parts) > 1 else 0
        else:
            split = "sqrt"
            model = d.get("model", "")
            label = _normalize_model_label(model)
            seed = d.get("seed", 0)
            # Fall back to filename for greedy bot etc
            if not label or label == model:
                # yc_bench_result_medium_1_greedy_bot.json
                stem_parts = f.stem.split("_", 4)
                if len(stem_parts) >= 5:
                    label = re.sub(r"^(openai|gemini|anthropic)_", "", stem_parts[4])
            label = LABEL_ALIASES.get(label, label)

        # Extract funds time series
        ts = d.get("time_series", {})
        funds_entries = ts.get("funds", [])
        funds_by_day = {}
        for entry in funds_entries:
            day = entry.get("time", "")[:10]
            funds_cents = entry.get("funds_cents", 0)
            funds_by_day[day] = funds_cents / 100

        # Summary stats
        terminal = d.get("terminal_reason", "unknown")
        final_funds = funds_by_day[max(funds_by_day)] if funds_by_day else 0

        model_runs[(split, label)].append({
            "seed": seed,
            "funds_by_day": funds_by_day,
            "final_funds": final_funds,
            "terminal": terminal,
            "turns": d.get("turns_completed", 0),
            "cost": d.get("total_cost_usd", 0),
        })

    return dict(model_runs)


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def _render_dashboard():
    all_runs = load_all_results()
    if not all_runs:
        st.warning("No results found in all_results/. Add result JSON files first.")
        return

    # Merge all runs by label (ignore split type)
    model_runs = defaultdict(list)
    for (split, label), runs in all_runs.items():
        model_runs[label].extend(runs)

    # View mode
    view_mode = st.radio("View", ["Per Seed", "Averaged", "Bar Chart"], horizontal=True, key="funds_view_mode")

    if view_mode == "Per Seed":
        all_seeds = sorted({r["seed"] for runs in model_runs.values() for r in runs})
        cols = st.columns(len(all_seeds)) if all_seeds else []
        for col, seed in zip(cols, all_seeds):
            with col:
                fig = go.Figure()
                for label in sorted(model_runs.keys()):
                    color = _model_color(label)
                    for r in model_runs[label]:
                        if r["seed"] != seed:
                            continue
                        fbd = r["funds_by_day"]
                        if not fbd:
                            continue
                        days = sorted(fbd.keys())
                        vals = [fbd[d] for d in days]
                        fig.add_trace(go.Scatter(
                            x=days, y=vals, mode="lines",
                            name=label,
                            line=dict(color=color, width=2),
                        ))
                fig.add_hline(y=200_000, line_dash="dash", line_color="#555")
                fig.update_layout(**_chart_layout(title=f"Seed {seed}", yaxis_title="Funds ($)", height=450))
                fig.update_yaxes(tickprefix="$", tickformat=",", rangemode="tozero")
                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    elif view_mode == "Averaged":
        fig = go.Figure()
        for label in sorted(model_runs.keys()):
            color = _model_color(label)
            runs = model_runs[label]
            all_days = set()
            series = []
            for r in runs:
                fbd = r["funds_by_day"]
                if fbd:
                    all_days.update(fbd.keys())
                    series.append(fbd)
            if not all_days or not series:
                continue
            common_days = sorted(all_days)
            aligned = []
            for s in series:
                s_days = sorted(s.keys())
                if not s_days:
                    continue
                vals, last_val, si = [], 200_000, 0
                for d in common_days:
                    while si < len(s_days) and s_days[si] <= d:
                        last_val = s[s_days[si]]
                        si += 1
                    vals.append(last_val)
                aligned.append(vals)
            if not aligned:
                continue
            arr = np.array(aligned)
            mean = arr.mean(axis=0)
            fig.add_trace(go.Scatter(
                x=common_days, y=mean, mode="lines",
                name=f"{label} (n={len(aligned)})",
                line=dict(color=color, width=3),
            ))
            if len(aligned) > 1:
                lo, hi = arr.min(axis=0), arr.max(axis=0)
                _r, _g, _b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
                fig.add_trace(go.Scatter(
                    x=list(common_days) + list(common_days)[::-1],
                    y=list(hi) + list(lo[::-1]),
                    fill="toself", fillcolor=f"rgba({_r},{_g},{_b},0.1)",
                    line=dict(color="rgba(0,0,0,0)"),
                    showlegend=False, hoverinfo="skip",
                ))
        fig.add_hline(y=200_000, line_dash="dash", line_color="#555",
                       annotation_text="Starting $200K")
        fig.update_layout(**_chart_layout(title="Funds Over Time (averaged across seeds)", yaxis_title="Funds ($)", height=500))
        fig.update_yaxes(tickprefix="$", tickformat=",", rangemode="tozero")
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    elif view_mode == "Bar Chart":
        model_finals = {}
        for label, runs in model_runs.items():
            finals = [r["final_funds"] for r in runs if r["funds_by_day"]]
            if finals:
                model_finals[label] = {"avg": sum(finals) / len(finals), "n": len(finals)}

        sorted_models = sorted(model_finals.items(), key=lambda x: -x[1]["avg"])
        labels = [m for m, _ in sorted_models]
        avgs = [max(0, d["avg"]) for _, d in sorted_models]
        colors = [_model_color(m) for m in labels]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=labels, y=avgs,
            marker_color=colors,
            text=[f"${v:,.0f}" for v in avgs],
            textposition="outside",
            textfont=dict(color=TEXT_COLOR, size=12),
        ))
        fig.add_hline(y=200_000, line_dash="dash", line_color="#555",
                       annotation_text="Starting $200K")
        fig.add_hline(y=0, line_color=ACCENT_RED, line_width=1)
        fig.update_layout(
            **_chart_layout(title="Average Final Funds Across Seeds", yaxis_title="Funds ($)", height=500),
            showlegend=False,
        )
        fig.update_yaxes(tickprefix="$", tickformat=",", rangemode="tozero")
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})



# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

st.markdown("""
<div style="display: flex; align-items: center; gap: 12px; margin-bottom: 8px;">
    <span style="font-size: 1.6rem; font-weight: 700; color: #e0e0e0;">YC-Bench Results</span>
</div>
""", unsafe_allow_html=True)

_render_dashboard()

# Auto-refresh
time.sleep(10)
st.rerun()
