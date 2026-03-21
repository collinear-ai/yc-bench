"""Streamlit dashboard — live-monitor YC-Bench runs.

Usage:
    uv run streamlit run scripts/watch_dashboard.py                           # multi-model overview (auto-discovers db/)
    uv run streamlit run scripts/watch_dashboard.py -- db/medium_1_model.db   # single-run detail

Automatically overlays the greedy bot baseline if a matching *_greedy_bot.db exists
in the same directory (e.g. db/medium_1_greedy_bot.db).
"""
from __future__ import annotations

import json as _json
import os
import sys
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import re
import sqlite3
from collections import defaultdict

import numpy as np
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from yc_bench.db.models.client import Client, ClientTrust
from yc_bench.db.models.company import Company, CompanyPrestige
from yc_bench.db.models.employee import Employee
from yc_bench.db.models.ledger import LedgerEntry
from yc_bench.db.models.sim_state import SimState
from yc_bench.db.models.task import Task, TaskRequirement, TaskStatus
from yc_bench.db.session import build_engine, build_session_factory, session_scope

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

CHART_COLORS = [
    "#4da6ff", "#00d4aa", "#ff8c42", "#b197fc",
    "#ffd43b", "#ff4b6e", "#45c4b0", "#e599f7",
    "#69db7c", "#ffa94d",
]

# ---------------------------------------------------------------------------
# Page config & CSS
# ---------------------------------------------------------------------------

st.set_page_config(page_title="YC-Bench Live", layout="wide", page_icon="📊")

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
    .metric-value.blue { color: #4da6ff; }
    .metric-value.yellow { color: #ffd43b; }
    .metric-value.purple { color: #b197fc; }
    .section-header {
        color: #e0e0e0;
        font-size: 1.1rem;
        font-weight: 600;
        margin: 32px 0 16px 0;
        padding-bottom: 8px;
        border-bottom: 2px solid #2a2d35;
    }
    .db-path {
        color: #8b8d93;
        font-size: 0.75rem;
        font-family: monospace;
    }
    div[data-testid="stVerticalBlock"] > div { gap: 0.5rem; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Parse DB path
# ---------------------------------------------------------------------------

db_path = None
args = sys.argv[1:]
for a in args:
    if a.endswith(".db"):
        db_path = a
        break

MULTI_MODEL_MODE = db_path is None

if MULTI_MODEL_MODE:
    # Auto-discover all DBs — show multi-model overview
    _all_dbs = sorted(Path("db").glob("*.db"))
    if not _all_dbs:
        st.error("No DB files found in db/. Run some experiments first.")
        st.stop()
    # Use first DB to bootstrap config detection
    db_file = _all_dbs[0]
else:
    db_file = Path(db_path)
    if not db_file.exists():
        st.error(f"DB not found: {db_file}")
        st.stop()

# Auto-detect config name from DB filename (e.g. "medium_1_model.db" -> "medium")
_db_stem_parts = db_file.stem.split("_")
if _db_stem_parts:
    os.environ.setdefault("YC_BENCH_EXPERIMENT", _db_stem_parts[0])

from yc_bench.config import get_world_config


@st.cache_resource
def get_factory(path: str):
    engine = build_engine(f"sqlite:///{path}")
    return build_session_factory(engine)


factory = get_factory(str(db_file))

# ---------------------------------------------------------------------------
# Auto-detect greedy bot baseline DB
# ---------------------------------------------------------------------------

def _find_all_peer_dbs(primary: Path) -> list[tuple[str, Path]]:
    """Find all DBs with the same config+seed prefix (other models + greedy bot)."""
    parts = primary.stem.split("_")
    if len(parts) < 2:
        return []
    prefix = "_".join(parts[:2])  # e.g. "medium_2"
    peers = []
    for p in sorted(primary.parent.glob(f"{prefix}_*.db")):
        if p == primary:
            continue
        # Derive a label from the filename
        model_part = p.stem[len(prefix) + 1:]  # e.g. "greedy_bot" or "openai_gpt-5.2-2025-12-11"
        label = model_part.replace("_", " ").replace("-", " ")
        if "greedy" in label:
            label = "Greedy Bot"
        else:
            # Use the model name directly from the filename
            label = model_part
        peers.append((label, p))
    return peers


peer_dbs = _find_all_peer_dbs(db_file)
peer_factories = [(label, get_factory(str(p))) for label, p in peer_dbs]

# Keep backward compat
baseline_db = None
baseline_factory = None
for label, p in peer_dbs:
    if "greedy" in p.stem.lower():
        baseline_db = p
        baseline_factory = get_factory(str(p))
        break


def query_funds_only(fct):
    with session_scope(fct) as db:
        sim = db.query(SimState).first()
        if not sim:
            return [], []
        company = db.query(Company).filter(Company.id == sim.company_id).one()
        company_id = sim.company_id
        ledger = (
            db.query(LedgerEntry)
            .filter(LedgerEntry.company_id == company_id)
            .order_by(LedgerEntry.occurred_at)
            .all()
        )
        total_delta = sum(int(e.amount_cents) for e in ledger)
        initial_funds = int(company.funds_cents) - total_delta
        running = initial_funds
        times, vals = [], []
        for e in ledger:
            running += int(e.amount_cents)
            times.append(e.occurred_at)
            vals.append(running / 100)
        return times, vals


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
# Query DB state
# ---------------------------------------------------------------------------

def query_state():
    wc = get_world_config()

    with session_scope(factory) as db:
        sim = db.query(SimState).first()
        if not sim:
            return None
        company = db.query(Company).filter(Company.id == sim.company_id).one()
        company_id = sim.company_id

        # Funds time series
        ledger = (
            db.query(LedgerEntry)
            .filter(LedgerEntry.company_id == company_id)
            .order_by(LedgerEntry.occurred_at)
            .all()
        )
        total_delta = sum(int(e.amount_cents) for e in ledger)
        initial_funds = int(company.funds_cents) - total_delta
        running = initial_funds
        funds_times, funds_vals, funds_categories = [], [], []
        for e in ledger:
            running += int(e.amount_cents)
            funds_times.append(e.occurred_at)
            funds_vals.append(running / 100)
            funds_categories.append(e.category.value if hasattr(e.category, "value") else str(e.category))

        # Tasks
        tasks = db.query(Task).filter(Task.company_id == company_id).all()
        task_counts = {}
        for s in TaskStatus:
            task_counts[s.value] = sum(1 for t in tasks if t.status == s)

        completed_tasks = [t for t in tasks if t.status == TaskStatus.COMPLETED_SUCCESS]
        total_reward = sum(t.reward_funds_cents for t in completed_tasks)

        # Prestige (current snapshot)
        prestige_rows = db.query(CompanyPrestige).filter(
            CompanyPrestige.company_id == company_id
        ).all()
        prestige = {
            (p.domain.value if hasattr(p.domain, "value") else str(p.domain)): float(p.prestige_level)
            for p in prestige_rows
        }

        # Prestige time series
        all_domains = sorted(prestige.keys())
        completed_ordered = (
            db.query(Task)
            .filter(
                Task.company_id == company_id,
                Task.completed_at.isnot(None),
                Task.status.in_([TaskStatus.COMPLETED_SUCCESS, TaskStatus.COMPLETED_FAIL]),
            )
            .order_by(Task.completed_at)
            .all()
        )

        task_domain_map = {}
        for t in completed_ordered:
            reqs = db.query(TaskRequirement).filter(TaskRequirement.task_id == t.id).all()
            task_domain_map[str(t.id)] = [
                r.domain.value if hasattr(r.domain, "value") else str(r.domain)
                for r in reqs
            ]

        domain_levels = {d: wc.initial_prestige_level for d in all_domains}
        last_event_time = None
        prestige_series = {d: {"times": [], "levels": []} for d in all_domains}

        if completed_ordered:
            first_time = completed_ordered[0].completed_at
            for domain in all_domains:
                prestige_series[domain]["times"].append(first_time)
                prestige_series[domain]["levels"].append(round(domain_levels[domain], 4))
            last_event_time = first_time

        for t in completed_ordered:
            if last_event_time and t.completed_at > last_event_time:
                days = (t.completed_at - last_event_time).total_seconds() / 86400
                decay = wc.prestige_decay_per_day * days
                for d in all_domains:
                    domain_levels[d] = max(wc.prestige_min, domain_levels[d] - decay)

            domains = task_domain_map.get(str(t.id), [])
            delta = float(t.reward_prestige_delta) if t.reward_prestige_delta else 0.0
            is_success = (t.status == TaskStatus.COMPLETED_SUCCESS)
            for domain in domains:
                if is_success:
                    domain_levels[domain] = min(wc.prestige_max, domain_levels[domain] + delta)
                else:
                    penalty = wc.penalty_fail_multiplier * delta
                    domain_levels[domain] = max(wc.prestige_min, domain_levels[domain] - penalty)
                prestige_series[domain]["times"].append(t.completed_at)
                prestige_series[domain]["levels"].append(round(domain_levels[domain], 4))
            last_event_time = t.completed_at

        # Trust (current snapshot)
        trust_rows = (
            db.query(ClientTrust, Client.name, Client.tier)
            .join(Client, Client.id == ClientTrust.client_id)
            .filter(ClientTrust.company_id == company_id)
            .order_by(Client.name)
            .all()
        )
        trusts = [
            {"client": name, "trust": float(ct.trust_level), "tier": tier}
            for ct, name, tier in trust_rows
        ]
        client_names = {str(ct.client_id): name for ct, name, _ in trust_rows}
        client_tiers = {str(ct.client_id): tier for ct, _, tier in trust_rows}

        # Trust time series
        client_tasks = (
            db.query(Task)
            .filter(
                Task.company_id == company_id,
                Task.client_id.isnot(None),
                Task.completed_at.isnot(None),
                Task.status.in_([TaskStatus.COMPLETED_SUCCESS, TaskStatus.COMPLETED_FAIL]),
            )
            .order_by(Task.completed_at)
            .all()
        )

        trust_levels = {str(ct.client_id): 0.0 for ct, _, _ in trust_rows}
        last_trust_time = None
        trust_series = {name: {"times": [], "levels": []} for name in client_names.values()}

        if client_tasks:
            first_time = client_tasks[0].completed_at
            for cid, name in client_names.items():
                trust_series[name]["times"].append(first_time)
                trust_series[name]["levels"].append(0.0)
            last_trust_time = first_time

        for t in client_tasks:
            cid = str(t.client_id)
            if cid not in trust_levels:
                continue

            if last_trust_time and t.completed_at and t.completed_at > last_trust_time:
                days_elapsed = (t.completed_at - last_trust_time).total_seconds() / 86400
                decay = wc.trust_decay_per_day * days_elapsed
                for k in trust_levels:
                    trust_levels[k] = max(wc.trust_min, trust_levels[k] - decay)

            if t.status == TaskStatus.COMPLETED_SUCCESS:
                ratio = trust_levels[cid] / wc.trust_max
                gain = wc.trust_gain_base * ((1 - ratio) ** wc.trust_gain_diminishing_power)
                trust_levels[cid] = min(wc.trust_max, trust_levels[cid] + gain)
            else:
                trust_levels[cid] = max(wc.trust_min, trust_levels[cid] - wc.trust_fail_penalty)

            name = client_names[cid]
            trust_series[name]["times"].append(t.completed_at)
            trust_series[name]["levels"].append(round(trust_levels[cid], 4))
            last_trust_time = t.completed_at

        # Employees
        emp_count = db.query(Employee).filter(Employee.company_id == company_id).count()

        # Monthly payroll
        total_payroll = sum(
            e.salary_cents for e in db.query(Employee).filter(Employee.company_id == company_id).all()
        )

        return {
            "sim_time": sim.sim_time,
            "funds_cents": company.funds_cents,
            "funds_times": funds_times,
            "funds_vals": funds_vals,
            "funds_categories": funds_categories,
            "task_counts": task_counts,
            "total_reward": total_reward,
            "completed": task_counts.get("completed_success", 0),
            "failed": task_counts.get("completed_fail", 0),
            "active": task_counts.get("active", 0),
            "planned": task_counts.get("planned", 0),
            "prestige": prestige,
            "prestige_series": prestige_series,
            "trusts": trusts,
            "trust_series": trust_series,
            "client_names": client_names,
            "client_tiers": client_tiers,
            "emp_count": emp_count,
            "monthly_payroll": total_payroll,
        }


# ---------------------------------------------------------------------------
# Load transcript
# ---------------------------------------------------------------------------

def _load_transcript(primary_db: Path) -> list[dict]:
    """Load live transcript JSONL file, or fall back to result JSON."""
    transcript_path = primary_db.with_suffix(".transcript.jsonl")
    if transcript_path.exists():
        entries = []
        try:
            with open(transcript_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(_json.loads(line))
        except Exception:
            pass
        if entries:
            return entries

    # Fall back to result JSON
    result_path = Path("results") / f"yc_bench_result_{primary_db.stem}.json"
    if result_path.exists():
        try:
            with open(result_path) as f:
                data = _json.load(f)
            if "transcript" in data:
                return data["transcript"]
            if "episodes" in data and data["episodes"]:
                return data["episodes"][-1].get("transcript", [])
        except Exception:
            pass
    return []


# ---------------------------------------------------------------------------
# Multi-model overview (when no DB arg given)
# ---------------------------------------------------------------------------

MODEL_COLORS = {
    "gpt-5.4": "#4da6ff",
    "gpt-5.4-nano": "#ff8c42",
    "gpt-5.4-mini": "#ffd43b",
    "gemini-3.1-pro-preview": "#00d4aa",
    "gemini-3-flash-preview": "#b197fc",
    "claude-sonnet-4-6": "#e599f7",
    "greedy_bot": "#ff4b6e",
}


def _model_color(label: str) -> str:
    for key, color in MODEL_COLORS.items():
        if key in label:
            return color
    return "#8b8d93"


def _parse_db_stem(stem: str) -> tuple[str, int, str]:
    """Parse 'medium_1_openai_gpt-5.4' -> (config, seed, model_label)."""
    parts = stem.split("_", 2)
    if len(parts) < 3:
        return stem, 0, stem
    config = parts[0]
    try:
        seed = int(parts[1])
    except ValueError:
        return stem, 0, stem
    raw = parts[2]
    label = re.sub(r"^(openai|gemini|anthropic)_", "", raw)
    return config, seed, label


def _read_db_summary(db_path: Path) -> dict | None:
    """Read key metrics from a DB via raw sqlite3 (no ORM)."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT sim_time, horizon_end FROM sim_state LIMIT 1").fetchone()
        co = conn.execute("SELECT funds_cents FROM companies LIMIT 1").fetchone()
        if not row or not co:
            return None
        ok = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='completed_success'").fetchone()[0]
        fail = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='completed_fail'").fetchone()[0]
        ledger = conn.execute(
            "SELECT occurred_at, amount_cents FROM ledger_entries ORDER BY occurred_at"
        ).fetchall()
        running = 20_000_000  # default starting funds
        funds_by_day = {}
        for occ, amt in ledger:
            running += amt
            funds_by_day[occ[:10]] = running
        sim_time, horizon_end = row
        funds = co[0]
        return {
            "sim_time": sim_time[:10],
            "funds": funds / 100,
            "ok": ok, "fail": fail,
            "done": sim_time >= horizon_end or funds < 0,
            "bankrupt": funds < 0,
            "funds_by_day": {k: v / 100 for k, v in funds_by_day.items()},
        }
    except Exception:
        return None
    finally:
        conn.close()


def _render_multi_model():
    """Render the multi-model overview dashboard."""
    all_dbs = sorted(Path("db").glob("*.db"))
    if not all_dbs:
        st.warning("No DB files in db/")
        return

    model_runs = defaultdict(list)
    for p in all_dbs:
        config, seed, label = _parse_db_stem(p.stem)
        data = _read_db_summary(p)
        if data:
            model_runs[label].append((seed, p, data))

    # --- Per-seed funds curves ---
    st.markdown('<div class="section-header">Funds Over Time (per seed)</div>', unsafe_allow_html=True)
    fig1 = go.Figure()
    for label in sorted(model_runs.keys()):
        color = _model_color(label)
        for seed, _, data in sorted(model_runs[label], key=lambda x: x[0]):
            fbd = data.get("funds_by_day", {})
            if not fbd:
                continue
            days = sorted(fbd.keys())
            vals = [fbd[d] for d in days]
            fig1.add_trace(go.Scatter(
                x=days, y=vals, mode="lines",
                name=f"{label} (s{seed})",
                line=dict(color=color, width=1.5, dash="dot" if seed > 1 else "solid"),
                opacity=0.7,
            ))
    fig1.add_hline(y=200_000, line_dash="dash", line_color="#555",
                   annotation_text="Starting $200K")
    fig1.update_layout(**_chart_layout(yaxis_title="Funds ($)", height=500))
    fig1.update_yaxes(tickprefix="$", tickformat=",")
    st.plotly_chart(fig1, use_container_width=True, config={"displayModeBar": False})

    # --- Averaged funds curves ---
    st.markdown('<div class="section-header">Funds Over Time (averaged across seeds)</div>', unsafe_allow_html=True)
    fig2 = go.Figure()
    for label in sorted(model_runs.keys()):
        color = _model_color(label)
        runs = model_runs[label]
        all_days = set()
        series = []
        for seed, _, data in runs:
            fbd = data.get("funds_by_day", {})
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
        fig2.add_trace(go.Scatter(
            x=common_days, y=mean, mode="lines",
            name=f"{label} (n={len(aligned)})",
            line=dict(color=color, width=3),
        ))
        if len(aligned) > 1:
            lo, hi = arr.min(axis=0), arr.max(axis=0)
            # Convert hex color to rgba for fill
            _r, _g, _b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
            fig2.add_trace(go.Scatter(
                x=list(common_days) + list(common_days)[::-1],
                y=list(hi) + list(lo[::-1]),
                fill="toself", fillcolor=f"rgba({_r},{_g},{_b},0.1)",
                line=dict(color="rgba(0,0,0,0)"),
                showlegend=False, hoverinfo="skip",
            ))
    fig2.add_hline(y=200_000, line_dash="dash", line_color="#555",
                   annotation_text="Starting $200K")
    fig2.update_layout(**_chart_layout(yaxis_title="Funds ($) — averaged", height=500))
    fig2.update_yaxes(tickprefix="$", tickformat=",")
    st.plotly_chart(fig2, use_container_width=True, config={"displayModeBar": False})



# ---------------------------------------------------------------------------
# Header + metrics (always visible)
# ---------------------------------------------------------------------------

st.markdown("""
<div style="display: flex; align-items: center; gap: 12px; margin-bottom: 8px;">
    <span style="font-size: 1.6rem; font-weight: 700; color: #e0e0e0;">YC-Bench</span>
    <span style="background: #00d4aa22; color: #00d4aa; padding: 2px 10px; border-radius: 20px;
                 font-size: 0.75rem; font-weight: 600;">LIVE</span>
</div>
""", unsafe_allow_html=True)
# ---------------------------------------------------------------------------
# Multi-model overview (when no DB arg given)
# ---------------------------------------------------------------------------
if MULTI_MODEL_MODE:
    _render_multi_model()
    st.markdown("---")

    # Let user pick a run for detail view below
    all_dbs = sorted(Path("db").glob("*.db"))
    db_options = {p.stem: p for p in all_dbs}
    if db_options:
        selected = st.selectbox("Select run for detail view", list(db_options.keys()), index=0)
        db_file = db_options[selected]
        # Re-initialize factory and peers for selected DB
        factory = get_factory(str(db_file))
        peer_dbs = _find_all_peer_dbs(db_file)
        peer_factories = [(label, get_factory(str(p))) for label, p in peer_dbs]
        baseline_db = None
        baseline_factory = None
        for label, p in peer_dbs:
            if "greedy" in p.stem.lower():
                baseline_db = p
                baseline_factory = get_factory(str(p))
                break
    else:
        st.warning("No DB files found.")
        st.stop()

baseline_label = f' &nbsp;|&nbsp; baseline: <span style="color:#ff4b6e">{baseline_db.name}</span>' if baseline_db else ""
st.markdown(f'<div class="db-path">{db_file}{baseline_label}</div>', unsafe_allow_html=True)

state = query_state()
if state is None:
    st.warning("No simulation found in DB.")
    st.stop()

# Top metric cards
funds = state["funds_cents"] / 100
funds_color = "green" if funds > 0 else "red"
runway = round(funds / (state["monthly_payroll"] / 100), 1) if state["monthly_payroll"] > 0 else float("inf")
max_prestige = max(state["prestige"].values()) if state["prestige"] else 1.0
avg_prestige = sum(state["prestige"].values()) / len(state["prestige"]) if state["prestige"] else 1.0

cols = st.columns(5)
tasks_str = f'{state["completed"]}✓ {state["failed"]}✗ {state["active"]}⟳'
metrics = [
    ("Funds", f"${funds:,.0f}", funds_color),
    ("Sim Date", state["sim_time"].strftime("%b %d, %Y"), "blue"),
    ("Prestige", f"{max_prestige:.1f}", "purple"),
    ("Tasks", tasks_str, "green"),
    ("Runway", f"{runway:.0f}mo" if runway != float("inf") else "N/A", "yellow"),
]

for col, (label, value, color) in zip(cols, metrics):
    col.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">{label}</div>
        <div class="metric-value {color}">{value}</div>
    </div>
    """, unsafe_allow_html=True)

st.markdown("<div style='height: 24px'></div>", unsafe_allow_html=True)


# ===========================================================================
# TABS
# ===========================================================================

tab_charts, tab_world = st.tabs(["Charts", "World"])

# ---------------------------------------------------------------------------
# TAB 1: Charts
# ---------------------------------------------------------------------------

with tab_charts:

    # Funds chart
    if state["funds_times"]:
        st.markdown('<div class="section-header">Funds Over Time</div>', unsafe_allow_html=True)

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=state["funds_times"], y=state["funds_vals"],
            mode="lines", name="_".join(db_file.stem.split("_")[2:]),
            line=dict(color=ACCENT_GREEN, width=2),
            fill="tozeroy", fillcolor="rgba(0,212,170,0.08)",
        ))
        # Overlay all peer runs (other models + greedy bot)
        peer_colors = [ACCENT_RED, ACCENT_BLUE, ACCENT_YELLOW, ACCENT_PURPLE, ACCENT_ORANGE]
        for i, (peer_label, peer_fct) in enumerate(peer_factories):
            pl_times, pl_vals = query_funds_only(peer_fct)
            if pl_times:
                is_bot = "greedy" in peer_label.lower() or "bot" in peer_label.lower()
                fig.add_trace(go.Scatter(
                    x=pl_times, y=pl_vals,
                    mode="lines", name=peer_label,
                    line=dict(color=peer_colors[i % len(peer_colors)], width=2, dash="dot" if is_bot else "solid"),
                ))
                if pl_vals[-1] < 0:
                    fig.add_trace(go.Scatter(
                        x=[pl_times[-1]], y=[pl_vals[-1]],
                        mode="markers+text", name=f"{peer_label} Bankrupt",
                        marker=dict(color=peer_colors[i % len(peer_colors)], size=10, symbol="x"),
                        text=["BANKRUPT"], textposition="top center",
                        textfont=dict(color=peer_colors[i % len(peer_colors)], size=10),
                        showlegend=False,
                    ))
        # Annotate dips — group payroll by month, always show disputes
        if len(state["funds_times"]) > 1:
            cats = state.get("funds_categories", [])

            # Group payroll into monthly totals
            payroll_months = {}  # "YYYY-MM" -> {"total": int, "time": datetime, "val": float}
            disputes_list = []

            for i in range(1, len(state["funds_vals"])):
                delta = state["funds_vals"][i] - state["funds_vals"][i - 1]
                t = state["funds_times"][i]
                v = state["funds_vals"][i]
                cat = cats[i] if i < len(cats) else ""

                if cat == "payment_dispute":
                    disputes_list.append((t, v, delta))
                elif cat == "monthly_payroll" and delta < 0:
                    key = t.strftime("%Y-%m")
                    if key not in payroll_months:
                        payroll_months[key] = {"total": 0, "time": t, "val": v}
                    payroll_months[key]["total"] += delta
                    payroll_months[key]["val"] = v  # use final value after all deductions

            ay_flip = -1
            for t, v, delta in disputes_list:
                ay_flip *= -1
                fig.add_annotation(
                    x=t, y=v, text=f"Dispute -${abs(delta):,.0f}",
                    showarrow=True, arrowhead=2, arrowsize=0.8, arrowcolor=ACCENT_RED,
                    font=dict(size=9, color=ACCENT_RED), bgcolor="#1a1d23", bordercolor=ACCENT_RED,
                    borderwidth=1, borderpad=3, ax=0, ay=ay_flip * 35,
                )

            for key, pm in payroll_months.items():
                fig.add_annotation(
                    x=pm["time"], y=pm["val"], text=f"Payroll -${abs(pm['total']):,.0f}",
                    showarrow=False,
                    font=dict(size=8, color=TEXT_MUTED), yshift=-14,
                )

        fig.add_hline(y=0, line_dash="dash", line_color=ACCENT_RED, opacity=0.3)
        show_legend = len(peer_factories) > 0
        fig.update_layout(**_chart_layout(yaxis_title="USD ($)", show_legend=show_legend))
        fig.update_yaxes(tickprefix="$", tickformat=",")
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # Prestige & Trust side by side
    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown('<div class="section-header">Prestige by Domain</div>', unsafe_allow_html=True)

        has_series = any(len(s["times"]) > 0 for s in state["prestige_series"].values())
        if has_series:
            fig = go.Figure()
            for i, (domain, series) in enumerate(sorted(state["prestige_series"].items())):
                if not series["times"]:
                    continue
                fig.add_trace(go.Scatter(
                    x=series["times"], y=series["levels"],
                    mode="lines+markers", name=domain.replace("_", " ").title(),
                    line=dict(color=CHART_COLORS[i % len(CHART_COLORS)], width=2),
                    marker=dict(size=3),
                ))
            layout = _chart_layout(yaxis_title="Prestige Level", height=350)
            layout["yaxis"]["range"] = [0.5, 10.5]
            fig.update_layout(**layout)
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        elif state["prestige"]:
            domains = sorted(state["prestige"].keys())
            levels = [state["prestige"][d] for d in domains]
            labels = [d.replace("_", " ").title() for d in domains]
            fig = go.Figure(go.Bar(
                x=labels, y=levels,
                marker_color=[CHART_COLORS[i % len(CHART_COLORS)] for i in range(len(domains))],
                marker_line=dict(width=0),
            ))
            layout = _chart_layout(yaxis_title="Level", height=350, show_legend=False)
            layout["yaxis"]["range"] = [0, 10.5]
            fig.update_layout(**layout)
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    with col_right:
        st.markdown('<div class="section-header">Client Trust</div>', unsafe_allow_html=True)

        has_trust_series = any(len(s["times"]) > 0 for s in state["trust_series"].values())
        if has_trust_series:
            fig = go.Figure()
            sorted_clients = sorted(
                state["trust_series"].items(),
                key=lambda x: x[1]["levels"][-1] if x[1]["levels"] else 0,
                reverse=True,
            )
            for i, (client, series) in enumerate(sorted_clients):
                if not series["times"]:
                    continue
                tier = None
                for cid, name in state["client_names"].items():
                    if name == client:
                        tier = state["client_tiers"].get(cid)
                        break
                label = f"{client} [{tier}]" if tier else client
                fig.add_trace(go.Scatter(
                    x=series["times"], y=series["levels"],
                    mode="lines", name=label,
                    line=dict(color=CHART_COLORS[i % len(CHART_COLORS)], width=2),
                ))
            layout = _chart_layout(yaxis_title="Trust Level", height=350)
            layout["yaxis"]["range"] = [-0.2, 5.5]
            fig.update_layout(**layout)
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        elif state["trusts"]:
            df_t = pd.DataFrame(state["trusts"])
            df_t["label"] = df_t.apply(lambda r: f"{r['client']} [{r['tier']}]", axis=1)
            df_t = df_t.sort_values("trust", ascending=True)
            fig = go.Figure(go.Bar(
                x=df_t["trust"], y=df_t["label"],
                orientation="h",
                marker_color=ACCENT_BLUE,
                marker_line=dict(width=0),
            ))
            layout = _chart_layout(height=350, show_legend=False)
            layout["xaxis"]["range"] = [0, 5.5]
            layout["xaxis"]["title"] = "Trust Level"
            layout["margin"]["l"] = 140
            fig.update_layout(**layout)
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # Current trust snapshot
    if state["trusts"]:
        st.markdown('<div class="section-header">Current Trust Snapshot</div>', unsafe_allow_html=True)
        df_t = pd.DataFrame(state["trusts"])
        df_t["label"] = df_t.apply(lambda r: f"{r['client']} [{r['tier']}]", axis=1)
        df_t = df_t.sort_values("trust", ascending=True)

        colors = []
        for _, row in df_t.iterrows():
            t = row["trust"]
            if t >= 3.0:
                colors.append(ACCENT_GREEN)
            elif t >= 1.0:
                colors.append(ACCENT_BLUE)
            elif t > 0:
                colors.append(ACCENT_YELLOW)
            else:
                colors.append(GRID_COLOR)

        fig = go.Figure(go.Bar(
            x=df_t["trust"], y=df_t["label"],
            orientation="h",
            marker_color=colors,
            marker_line=dict(width=0),
            text=[f"{t:.2f}" for t in df_t["trust"]],
            textposition="outside",
            textfont=dict(size=11, color=TEXT_MUTED),
        ))
        layout = _chart_layout(height=max(200, len(df_t) * 35 + 60), show_legend=False)
        layout["xaxis"]["range"] = [0, 5.5]
        layout["xaxis"]["title"] = "Trust Level"
        layout["margin"]["l"] = 160
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

def _esc(s: str) -> str:
    """Escape HTML."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# ---------------------------------------------------------------------------
# TAB 2: World State — god mode view
# ---------------------------------------------------------------------------

def query_world():
    """Query full world state including hidden info."""
    wc = get_world_config()
    with session_scope(factory) as db:
        sim = db.query(SimState).first()
        if not sim:
            return None
        company_id = sim.company_id

        from yc_bench.db.models.task import TaskAssignment
        from yc_bench.db.models.employee import EmployeeSkillRate
        from yc_bench.db.models.ledger import LedgerCategory
        from sqlalchemy import func as sqlfunc

        # ── Employees ──
        employees = db.query(Employee).filter(Employee.company_id == company_id).order_by(Employee.name).all()
        emp_data = []
        for emp in employees:
            assignments = (
                db.query(TaskAssignment, Task)
                .join(Task, Task.id == TaskAssignment.task_id)
                .filter(TaskAssignment.employee_id == emp.id, Task.status.in_([TaskStatus.PLANNED, TaskStatus.ACTIVE]))
                .all()
            )
            tasks_assigned = [{"title": t.title, "status": t.status.value} for _, t in assignments]
            skills = db.query(EmployeeSkillRate).filter(EmployeeSkillRate.employee_id == emp.id).all()
            skill_map = {
                (s.domain.value if hasattr(s.domain, "value") else str(s.domain)): round(float(s.rate_domain_per_hour), 2)
                for s in skills
            }
            # Count completed tasks for this employee
            completed_n = (
                db.query(sqlfunc.count(TaskAssignment.task_id))
                .join(Task, Task.id == TaskAssignment.task_id)
                .filter(TaskAssignment.employee_id == emp.id, Task.status == TaskStatus.COMPLETED_SUCCESS)
                .scalar() or 0
            )
            emp_data.append({
                "name": emp.name, "tier": emp.tier,
                "salary_cents": emp.salary_cents, "skills": skill_map,
                "tasks": tasks_assigned, "completed": completed_n,
                "hours_per_day": float(emp.work_hours_per_day),
            })

        # ── Clients (with hidden loyalty) ──
        clients_raw = (
            db.query(Client, ClientTrust)
            .join(ClientTrust, ClientTrust.client_id == Client.id)
            .filter(ClientTrust.company_id == company_id)
            .order_by(Client.name)
            .all()
        )
        client_data = []
        for c, ct in clients_raw:
            success_n = db.query(sqlfunc.count(Task.id)).filter(
                Task.company_id == company_id, Task.client_id == c.id, Task.status == TaskStatus.COMPLETED_SUCCESS).scalar() or 0
            fail_n = db.query(sqlfunc.count(Task.id)).filter(
                Task.company_id == company_id, Task.client_id == c.id, Task.status == TaskStatus.COMPLETED_FAIL).scalar() or 0
            active_n = db.query(sqlfunc.count(Task.id)).filter(
                Task.company_id == company_id, Task.client_id == c.id, Task.status == TaskStatus.ACTIVE).scalar() or 0

            # Dispute stats (hidden info)
            completed_tasks = db.query(Task).filter(
                Task.company_id == company_id, Task.client_id == c.id, Task.status == TaskStatus.COMPLETED_SUCCESS).all()
            task_ids = [t.id for t in completed_tasks]
            dispute_n = 0
            dispute_total = 0
            listed_total = sum((t.advertised_reward_cents or t.reward_funds_cents) for t in completed_tasks)
            received_total = 0
            if task_ids:
                from yc_bench.db.models.ledger import LedgerEntry
                reward_sum = db.query(sqlfunc.sum(LedgerEntry.amount_cents)).filter(
                    LedgerEntry.company_id == company_id, LedgerEntry.category == LedgerCategory.TASK_REWARD,
                    LedgerEntry.ref_id.in_(task_ids)).scalar()
                received_total = int(reward_sum) if reward_sum else 0
                dispute_entries = db.query(LedgerEntry).filter(
                    LedgerEntry.company_id == company_id, LedgerEntry.category == LedgerCategory.PAYMENT_DISPUTE,
                    LedgerEntry.ref_id.in_(task_ids)).all()
                dispute_n = len(dispute_entries)
                dispute_total = sum(abs(int(e.amount_cents)) for e in dispute_entries)

            # Hidden mechanics — what would happen at current trust
            trust_val = float(ct.trust_level)
            loyalty_class = "RAT" if c.loyalty < -0.3 else ("LOYAL" if c.loyalty > 0.3 else "NEUTRAL")
            effects_active = loyalty_class == "RAT"
            scope_creep_pct = 0.0
            dispute_prob = 0.0
            if effects_active:
                intensity = abs(c.loyalty)
                scope_creep_pct = wc.scope_creep_max * intensity * 100
                dispute_prob = wc.dispute_prob_max * intensity * 100

            client_data.append({
                "name": c.name, "tier": c.tier, "trust": trust_val,
                "specialties": c.specialty_domains or [],
                "reward_mult": c.reward_multiplier,
                "loyalty": c.loyalty, "loyalty_class": loyalty_class,
                "active": active_n, "completed": success_n, "failed": fail_n,
                "listed_total": listed_total, "received_total": received_total,
                "dispute_n": dispute_n, "dispute_total": dispute_total,
                "effects_active": effects_active,
                "scope_creep_pct": scope_creep_pct, "dispute_prob": dispute_prob,
            })

        # ── Active tasks ──
        active_tasks = db.query(Task).filter(
            Task.company_id == company_id, Task.status.in_([TaskStatus.ACTIVE, TaskStatus.PLANNED])
        ).order_by(Task.accepted_at).all()
        task_data = []
        for t in active_tasks:
            reqs = db.query(TaskRequirement).filter(TaskRequirement.task_id == t.id).all()
            assigns = db.query(TaskAssignment).filter(TaskAssignment.task_id == t.id).all()
            emp_names = []
            for a in assigns:
                e = db.query(Employee).filter(Employee.id == a.employee_id).one_or_none()
                if e: emp_names.append(e.name)
            total_req = sum(float(r.required_qty) for r in reqs)
            total_done = sum(float(r.completed_qty) for r in reqs)
            pct = (total_done / total_req * 100) if total_req > 0 else 0
            domains = [{
                "domain": r.domain.value if hasattr(r.domain, "value") else str(r.domain),
                "done": int(r.completed_qty), "total": int(r.required_qty),
                "pct": int(float(r.completed_qty) / float(r.required_qty) * 100) if float(r.required_qty) > 0 else 0,
            } for r in reqs]
            client_name = ""
            client_loyalty_class = ""
            if t.client_id:
                cl = db.query(Client).filter(Client.id == t.client_id).one_or_none()
                if cl:
                    client_name = cl.name
                    client_loyalty_class = "RAT" if cl.loyalty < -0.3 else ("LOYAL" if cl.loyalty > 0.3 else "NEUTRAL")
            # Scope creep detection: compare advertised vs actual reward
            was_scope_creeped = False
            if t.advertised_reward_cents and t.advertised_reward_cents != t.reward_funds_cents:
                was_scope_creeped = True

            task_data.append({
                "title": t.title, "client": client_name,
                "client_loyalty": client_loyalty_class,
                "status": t.status.value, "reward": t.reward_funds_cents,
                "advertised_reward": t.advertised_reward_cents or t.reward_funds_cents,
                "prestige_req": t.required_prestige,
                "prestige_delta": float(t.reward_prestige_delta) if t.reward_prestige_delta else 0.0,
                "skill_boost_pct": float(t.skill_boost_pct) if t.skill_boost_pct else 0.0,
                "trust_req": int(t.required_trust) if t.required_trust else 0,
                "progress_pct": pct,
                "deadline": t.deadline,
                "at_risk": t.deadline and t.status == TaskStatus.ACTIVE and sim.sim_time > t.deadline,
                "domains": domains, "employees": emp_names,
                "was_scope_creeped": was_scope_creeped,
            })

        # ── Recent completed ──
        recent = db.query(Task).filter(
            Task.company_id == company_id,
            Task.status.in_([TaskStatus.COMPLETED_SUCCESS, TaskStatus.COMPLETED_FAIL])
        ).order_by(Task.completed_at.desc()).limit(10).all()
        recent_data = []
        for t in recent:
            cn = ""
            if t.client_id:
                cl = db.query(Client).filter(Client.id == t.client_id).one_or_none()
                if cl: cn = cl.name
            recent_data.append({
                "title": t.title, "client": cn,
                "success": t.success, "reward": t.reward_funds_cents,
                "completed_at": t.completed_at,
            })

        # ── Trust effects log ──
        from yc_bench.db.models.ledger import LedgerEntry
        from yc_bench.db.models.event import SimEvent, EventType

        # Payment disputes
        dispute_entries = (
            db.query(LedgerEntry)
            .filter(LedgerEntry.company_id == company_id, LedgerEntry.category == LedgerCategory.PAYMENT_DISPUTE)
            .order_by(LedgerEntry.occurred_at.desc())
            .all()
        )
        disputes = []
        for d in dispute_entries:
            # Find the task and client
            task_row = db.query(Task).filter(Task.id == d.ref_id).one_or_none()
            cn = ""
            if task_row and task_row.client_id:
                cl = db.query(Client).filter(Client.id == task_row.client_id).one_or_none()
                if cl: cn = cl.name
            disputes.append({
                "date": d.occurred_at, "amount": abs(int(d.amount_cents)),
                "client": cn, "task": task_row.title if task_row else "?",
            })

        # Scope-creeped tasks (advertised != actual reward or we detect it from task data)
        all_company_tasks = db.query(Task).filter(
            Task.company_id == company_id,
            Task.advertised_reward_cents.isnot(None),
        ).order_by(Task.accepted_at).all()
        scope_creeps = []
        for t in all_company_tasks:
            # We stored advertised_reward = reward at accept time, so they're equal
            # Scope creep inflates required_qty but keeps deadline based on original qty
            # We can detect it by checking if the client is a RAT and trust was above threshold
            if t.client_id:
                cl = db.query(Client).filter(Client.id == t.client_id).one_or_none()
                if cl and cl.loyalty < -0.3:
                    ct_row = db.query(ClientTrust).filter(
                        ClientTrust.company_id == company_id, ClientTrust.client_id == t.client_id
                    ).one_or_none()
                    # Check if this task was accepted when trust > threshold
                    # We can't know exact trust at accept time, but if task failed deadline it's a hint
                    # For god-mode, just flag all tasks from RAT clients
                    scope_creeps.append({
                        "title": t.title, "client": cl.name,
                        "accepted": t.accepted_at,
                        "status": t.status.value if hasattr(t.status, "value") else str(t.status),
                        "failed_deadline": t.status == TaskStatus.COMPLETED_FAIL,
                    })

        # Work reduction stats: count tasks from loyal clients
        loyal_tasks_completed = 0
        rat_tasks_completed = 0
        for t in all_company_tasks:
            if t.status != TaskStatus.COMPLETED_SUCCESS:
                continue
            if t.client_id:
                cl = db.query(Client).filter(Client.id == t.client_id).one_or_none()
                if cl:
                    if cl.loyalty > 0.3: loyal_tasks_completed += 1
                    elif cl.loyalty < -0.3: rat_tasks_completed += 1

        # Pending dispute events
        pending_disputes = db.query(SimEvent).filter(
            SimEvent.company_id == company_id,
            SimEvent.event_type == EventType.PAYMENT_DISPUTE,
            SimEvent.consumed == False,
        ).count()

        trust_effects = {
            "disputes": disputes,
            "total_disputed": sum(d["amount"] for d in disputes),
            "scope_creeps": scope_creeps,
            "scope_creep_fails": sum(1 for s in scope_creeps if s["failed_deadline"]),
            "loyal_completed": loyal_tasks_completed,
            "rat_completed": rat_tasks_completed,
            "pending_disputes": pending_disputes,
        }

        # ── Market overview ──
        from collections import defaultdict
        market_tasks = db.query(Task).filter(Task.status == TaskStatus.MARKET).all()
        market_by_prestige = defaultdict(lambda: {"count": 0, "total_reward": 0})
        for t in market_tasks:
            market_by_prestige[t.required_prestige]["count"] += 1
            market_by_prestige[t.required_prestige]["total_reward"] += t.reward_funds_cents
        market_overview = []
        for p in sorted(market_by_prestige):
            d = market_by_prestige[p]
            avg = d["total_reward"] / d["count"] if d["count"] else 0
            market_overview.append({"prestige": p, "count": d["count"], "avg_reward": avg})

        return {"employees": emp_data, "clients": client_data, "active_tasks": task_data,
                "recent": recent_data, "sim_time": sim.sim_time, "wc": wc,
                "trust_effects": trust_effects, "market": market_overview}


with tab_world:
    world = query_world()
    if world is None:
        st.warning("No simulation data.")
    else:
        god_mode = True  # always on

        # ── Legend ──
        st.markdown("""
        <div style="padding:10px 14px;background:#14161a;border-radius:8px;border:1px solid #2a2d3544;
                    margin-bottom:12px;font-size:0.72rem;color:#8b8d93;line-height:1.8;">
            <table style="width:100%;border-collapse:collapse;">
                <tr>
                    <td style="vertical-align:top;padding-right:24px;white-space:nowrap;">
                        <b style="color:#e0e0e0;font-size:0.75rem;">Tasks</b><br>
                        🔵 = in progress, ⏳ = planned (not started)<br>
                        Each domain shows a progress bar: <b style="color:#e0e0e0;">done / required units</b><br>
                        <span style="color:#ff4b6e;">⚠ OVERDUE</span> = deadline has passed<br>
                        <span style="color:#ff4b6e;">🐀 RAT</span> = client is adversarial<br>
                        <span style="color:#ff8c42;">📈 CREEP</span> = work was secretly inflated
                    </td>
                    <td style="vertical-align:top;padding-right:24px;white-space:nowrap;">
                        <b style="color:#e0e0e0;font-size:0.75rem;">Employees</b><br>
                        Skill bars = work rate per domain (0-10, higher = faster)<br>
                        <span style="color:#4da6ff;">JUNIOR</span> low pay, low skills ·
                        <span style="color:#ffd43b;">MID</span> ·
                        <span style="color:#b197fc;">SENIOR</span> high pay, high skills<br>
                        <span style="color:#ff4b6e;">IDLE</span> = not assigned to any task (wasting salary)
                    </td>
                    <td style="vertical-align:top;white-space:nowrap;">
                        <b style="color:#e0e0e0;font-size:0.75rem;">Clients</b><br>
                        <b style="color:#e0e0e0;">Trust</b> 0-5: builds with completed tasks, decays over time<br>
                        <b style="color:#e0e0e0;">Multiplier</b>: hidden reward scaling (agent can't see)<br>
                        <span style="color:#ff4b6e;">🐀 RAT</span> = scope creep + payment disputes at high trust<br>
                        <span style="color:#00d4aa;">✦ LOYAL</span> = work reduction at high trust<br>
                        <b style="color:#e0e0e0;">Listed vs Net</b> = reward promised vs actually received<br>
                        ✓ completed · ✗ failed · ⟳ in progress
                    </td>
                </tr>
            </table>
        </div>
        """, unsafe_allow_html=True)

        # ════════════ TWO-COLUMN GRID: Left = Tasks+Employees, Right = Clients ════════════
        col_left, col_right = st.columns([3, 2])

        # ── LEFT COLUMN: Active Tasks + Employees ──
        with col_left:
            st.markdown('<div class="section-header">Active Tasks</div>', unsafe_allow_html=True)
            if not world["active_tasks"]:
                st.markdown('<div style="color:#8b8d93; padding:8px;">No active tasks.</div>', unsafe_allow_html=True)

            for t in world["active_tasks"]:
                rw = f"${t['reward']/100:,.0f}"
                dl = t["deadline"].strftime("%b %d") if t["deadline"] else "—"
                sc = ACCENT_PURPLE if t["status"] == "active" else ACCENT_YELLOW
                icon = "🔵" if t["status"] == "active" else "⏳"
                pct = t["progress_pct"]
                bc = ACCENT_GREEN if pct >= 100 else (ACCENT_RED if t["at_risk"] else ACCENT_BLUE)
                risk = f'<span style="color:{ACCENT_RED};font-weight:700;"> ⚠ OVERDUE</span>' if t["at_risk"] else ""

                hidden = ""
                if god_mode:
                    if t.get("client_loyalty") == "RAT":
                        hidden += '<span style="background:#ff4b6e22;color:#ff4b6e;padding:1px 5px;border-radius:3px;font-size:0.6rem;font-weight:700;margin-left:3px;">🐀 RAT</span>'
                    if t.get("was_scope_creeped"):
                        hidden += '<span style="background:#ff8c4222;color:#ff8c42;padding:1px 5px;border-radius:3px;font-size:0.6rem;font-weight:700;margin-left:3px;">📈 CREEP</span>'

                dom_pills = ""
                for d in t["domains"]:
                    dp = d["pct"]; dc = ACCENT_GREEN if dp >= 100 else ACCENT_BLUE
                    w = min(dp, 100)
                    dom_pills += (
                        f'<div style="display:inline-flex;align-items:center;gap:6px;background:#1a1d23;border:1px solid #2a2d35;'
                        f'padding:4px 10px;border-radius:6px;margin:2px 4px 2px 0;font-size:0.78rem;">'
                        f'<span style="color:#e0e0e0;font-weight:600;">{d["domain"]}</span>'
                        f'<div style="background:#2a2d35;border-radius:3px;width:60px;height:7px;">'
                        f'<div style="background:{dc};width:{w}%;height:100%;border-radius:3px;"></div></div>'
                        f'<span style="color:#e0e0e0;">{d["done"]:,}/{d["total"]:,}</span>'
                        f'</div>')

                emps = ", ".join(t["employees"][:5]) if t["employees"] else f'<span style="color:{ACCENT_RED};">none</span>'

                trust_req_html = f'<span>Trust req: <b style="color:#ffd43b;">{t["trust_req"]}</b></span>' if t["trust_req"] else ''
                st.markdown(
                    f'<div style="border:1px solid {sc}33;border-radius:8px;padding:10px 12px;margin:5px 0;background:#14161a;">'
                    f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">'
                    f'<span>{icon} <b style="color:#e0e0e0;font-size:0.85rem;">{_esc(t["title"])}</b> {risk}{hidden}</span>'
                    f'<span style="color:#00d4aa;font-weight:700;">{rw}</span></div>'
                    f'<div style="display:flex;flex-wrap:wrap;gap:10px;color:#8b8d93;font-size:0.72rem;margin-bottom:4px;">'
                    f'<span>Client: <b style="color:#e0e0e0;">{_esc(t["client"]) or "—"}</b></span>'
                    f'<span>Min prestige: <b style="color:#e0e0e0;">{t["prestige_req"]}</b></span>'
                    f'<span>Deadline: <b style="color:#e0e0e0;">{dl}</b></span>'
                    f'<span>Prestige reward: <b style="color:#e0e0e0;">+{t["prestige_delta"]:.2f}</b></span>'
                    f'<span>Skill boost: <b style="color:#e0e0e0;">{t["skill_boost_pct"]*100:.1f}%</b></span>'
                    f'{trust_req_html}</div></div>',
                    unsafe_allow_html=True,
                )
                # Domains + employees in separate markdown call to avoid HTML length issues
                st.markdown(
                    f'<div style="padding:0 12px 10px 12px;margin-top:-8px;background:#14161a;'
                    f'border:1px solid {sc}33;border-top:none;border-radius:0 0 8px 8px;">'
                    f'{dom_pills}'
                    f'<div style="color:#8b8d93;font-size:0.7rem;margin-top:4px;">👥 {emps}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            # ── EMPLOYEES (compact grid — 2 per row) ──
            st.markdown('<div class="section-header">Employees</div>', unsafe_allow_html=True)
            emp_cols = st.columns(2)
            for i, emp in enumerate(world["employees"]):
                tc_map = {"junior": ACCENT_BLUE, "mid": ACCENT_YELLOW, "senior": ACCENT_PURPLE}
                tc = tc_map.get(emp["tier"], ACCENT_BLUE)
                idle = len(emp["tasks"]) == 0
                sal = f"${emp['salary_cents']/100:,.0f}"

                skills_html = ""
                for domain in sorted(emp["skills"]):
                    rate = emp["skills"][domain]
                    w = min(rate / 10.0 * 100, 100)
                    skills_html += (
                        f'<div style="display:flex;align-items:center;gap:4px;margin:1px 0;">'
                        f'<span style="color:#8b8d93;font-size:0.65rem;min-width:70px;">{domain}</span>'
                        f'<div style="background:#1a1d23;border-radius:2px;width:60px;height:5px;">'
                        f'<div style="background:{tc};width:{w:.0f}%;height:100%;border-radius:2px;"></div></div>'
                        f'<span style="color:#e0e0e0;font-size:0.65rem;">{rate:.1f}</span></div>')

                work = ""
                if idle:
                    work = f'<span style="color:{ACCENT_RED};font-size:0.7rem;font-weight:600;">IDLE</span>'
                else:
                    for ta in emp["tasks"]:
                        si = "🔵" if ta["status"] == "active" else "⏳"
                        work += f'<div style="font-size:0.7rem;">{si} {_esc(ta["title"])}</div>'

                bc = f"{ACCENT_RED}44" if idle else "#2a2d3544"
                with emp_cols[i % 2]:
                    st.markdown(f"""
                    <div style="border:1px solid {bc};border-radius:8px;padding:8px 10px;margin:3px 0;background:#14161a;">
                        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:3px;">
                            <span><b style="color:#e0e0e0;font-size:0.82rem;">{_esc(emp['name'])}</b>
                            <span style="background:{tc}22;color:{tc};padding:0px 5px;border-radius:3px;font-size:0.6rem;font-weight:700;">{emp['tier'].upper()}</span></span>
                            <span style="color:#8b8d93;font-size:0.68rem;">{sal}/mo</span>
                        </div>
                        <div style="display:flex;gap:12px;">
                            <div>{skills_html}</div>
                            <div style="flex:1;">{work}</div>
                        </div>
                        <div style="color:#8b8d93;font-size:0.62rem;margin-top:2px;">{emp['completed']} tasks completed · {emp['hours_per_day']:.0f}h/day</div>
                    </div>""", unsafe_allow_html=True)

        # ── RIGHT COLUMN: Clients ──
        with col_right:
            st.markdown('<div class="section-header">Clients</div>', unsafe_allow_html=True)
            for c in sorted(world["clients"], key=lambda x: -x["trust"]):
                trust = c["trust"]
                trust_pct = trust / 5.0 * 100
                tier_map = {"Standard": ACCENT_BLUE, "Premium": ACCENT_YELLOW, "Enterprise": ACCENT_PURPLE}
                tc = tier_map.get(c["tier"], ACCENT_BLUE)
                bar_c = ACCENT_GREEN if trust >= 3 else (ACCENT_BLUE if trust >= 1 else GRID_COLOR)

                lc = c["loyalty_class"]
                border_c = "#2a2d3544"
                loyalty_badge = ""
                hidden_row = ""
                if god_mode:
                    if lc == "RAT":
                        border_c = f"{ACCENT_RED}55"
                        loyalty_badge = f'<span style="background:#ff4b6e22;color:#ff4b6e;padding:1px 6px;border-radius:3px;font-size:0.62rem;font-weight:800;">🐀 RAT {c["loyalty"]:+.2f}</span>'
                    elif lc == "LOYAL":
                        border_c = f"{ACCENT_GREEN}33"
                        loyalty_badge = f'<span style="background:#00d4aa22;color:#00d4aa;padding:1px 6px;border-radius:3px;font-size:0.62rem;font-weight:800;">✦ LOYAL {c["loyalty"]:+.2f}</span>'
                    else:
                        loyalty_badge = f'<span style="background:#8b8d9322;color:#8b8d93;padding:1px 6px;border-radius:3px;font-size:0.62rem;font-weight:700;">— {c["loyalty"]:+.2f}</span>'

                    if c["effects_active"]:
                        hidden_row = (
                            f'<div style="background:#ff4b6e11;border:1px solid #ff4b6e33;border-radius:4px;'
                            f'padding:4px 8px;margin-top:4px;font-size:0.68rem;color:#ff8c8c;">'
                            f'Next task from this client: work inflated +{c["scope_creep_pct"]:.0f}%, '
                            f'{c["dispute_prob"]:.0f}% chance of clawing back up to '
                            f'{world["wc"].dispute_clawback_max * abs(c["loyalty"]) * (c["trust"] - world["wc"].loyalty_reveal_trust) / (world["wc"].trust_max - world["wc"].loyalty_reveal_trust) * 100:.0f}% of reward</div>')
                    elif lc == "RAT":
                        reveal = world["wc"].loyalty_reveal_trust
                        hidden_row = (
                            f'<div style="background:#2a2d3533;border-radius:4px;padding:3px 8px;margin-top:4px;'
                            f'font-size:0.68rem;color:#8b8d93;">'
                            f'RAT effects dormant until trust reaches {reveal:.1f} (currently {trust:.1f})</div>')

                record = f'{c["completed"]}✓'
                if c["failed"]: record += f' {c["failed"]}✗'
                if c["active"]: record += f' {c["active"]}⟳'

                finance = ""
                if god_mode and c["completed"] > 0:
                    net = c["received_total"] - c["dispute_total"]
                    lost = c["dispute_total"]
                    finance_parts = f'Promised: ${c["listed_total"]/100:,.0f}'
                    if lost > 0:
                        finance_parts += (
                            f' · Received: <span style="color:{ACCENT_RED};font-weight:600;">${net/100:,.0f}</span>'
                            f' · <span style="color:{ACCENT_RED};">{c["dispute_n"]} dispute{"s" if c["dispute_n"] != 1 else ""}'
                            f' (-${lost/100:,.0f})</span>')
                    else:
                        finance_parts += f' · Received: <span style="color:{ACCENT_GREEN};font-weight:600;">${net/100:,.0f}</span>'
                    finance = f'<div style="font-size:0.68rem;color:#8b8d93;margin-top:3px;">{finance_parts}</div>'

                spec = " · ".join(c["specialties"]) if c["specialties"] else "—"

                st.markdown(
                    f'<div style="border:1px solid {border_c};border-radius:8px;padding:10px 12px;margin:5px 0;background:#14161a;">'
                    f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">'
                    f'<div style="display:flex;align-items:center;gap:4px;flex-wrap:wrap;">'
                    f'<b style="color:#e0e0e0;font-size:0.85rem;">{_esc(c["name"])}</b>'
                    f'<span style="background:{tc}22;color:{tc};padding:0px 6px;border-radius:3px;font-size:0.6rem;font-weight:700;">{c["tier"]}</span>'
                    f'{loyalty_badge}</div>'
                    f'<div style="display:flex;align-items:center;gap:6px;">'
                    f'<div style="background:#1a1d23;border-radius:3px;width:60px;height:6px;">'
                    f'<div style="background:{bar_c};width:{min(trust_pct,100):.0f}%;height:100%;border-radius:3px;"></div></div>'
                    f'<span style="color:#e0e0e0;font-weight:700;font-size:0.8rem;">{trust:.1f}</span></div></div>'
                    f'<div style="display:flex;gap:12px;color:#8b8d93;font-size:0.7rem;">'
                    f'<span>{spec}</span>'
                    f'<span>{c["reward_mult"]:.2f}x</span>'
                    f'<span>{record}</span></div>'
                    f'{finance}{hidden_row}</div>',
                    unsafe_allow_html=True,
                )



    # ── ASSIGNMENT PATTERN ──
    if state.get("funds_times"):
        st.markdown('<div class="section-header">Assignment Pattern</div>', unsafe_allow_html=True)
        try:
            from yc_bench.db.models.task import TaskAssignment
            from sqlalchemy import func as sqla_func
            with session_scope(factory) as db2:
                sim2 = db2.query(SimState).first()
                if sim2:
                    completed = db2.query(Task).filter(
                        Task.company_id == sim2.company_id,
                        Task.completed_at.isnot(None),
                    ).order_by(Task.completed_at).all()
                    if completed:
                        assign_times = []
                        assign_counts = []
                        for t in completed:
                            cnt = db2.query(sqla_func.count(TaskAssignment.employee_id)).filter(
                                TaskAssignment.task_id == t.id
                            ).scalar()
                            assign_times.append(t.completed_at)
                            assign_counts.append(cnt)
                        fig_assign = go.Figure()
                        fig_assign.add_trace(go.Scatter(
                            x=assign_times, y=assign_counts,
                            mode="markers", name="employees/task",
                            marker=dict(color=ACCENT_GREEN, size=6, opacity=0.6),
                        ))
                        fig_assign.add_hline(y=4, line_dash="dash", line_color="gray", opacity=0.3,
                                            annotation_text="efficient (4)")
                        fig_assign.update_layout(**_chart_layout(
                            title="Employees Assigned Per Task", height=250,
                            yaxis_title="Count", show_legend=False))
                        st.plotly_chart(fig_assign, use_container_width=True, config={"displayModeBar": False})
        except Exception:
            pass

    # ── SCRATCHPAD ──
    try:
        from yc_bench.db.models.scratchpad import Scratchpad
        with session_scope(factory) as db3:
            sim3 = db3.query(SimState).first()
            if sim3:
                sp = db3.query(Scratchpad).filter(Scratchpad.company_id == sim3.company_id).one_or_none()
                if sp and sp.content:
                    st.markdown('<div class="section-header">Scratchpad</div>', unsafe_allow_html=True)
                    st.markdown(
                        f'<div style="background:#14161a;border:1px solid #2a2d35;border-radius:8px;'
                        f'padding:12px;font-size:0.8rem;color:#c0c0c0;white-space:pre-wrap;">'
                        f'{_esc(sp.content)}</div>',
                        unsafe_allow_html=True)
    except Exception:
        pass


# Auto-refresh
time.sleep(5)
st.rerun()
