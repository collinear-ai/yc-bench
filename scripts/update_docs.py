"""Aggregate benchmark results into the docs/ leaderboard.

Reads results/yc_bench_result_<config>_<seed>_<model_slug>.json, averages
month-end net worth across seeds, and updates:

  docs/static/data.json   monthly average funds per model
  docs/index.html         MODEL_CONFIG, SORT_ORDER, OPEN_SOURCE, leaderboard <tbody>

Conventions (matched to the existing published data):
  * A month's value is net worth at the END of that month (last ledger point
    at or before the month boundary).
  * A bankrupt seed contributes 0 from its bankruptcy month onward, and
    negative funds are clamped to 0 — this is how greedy_bot is recorded.
  * Net Worth in the table is the 2025-12 value; Bankrupt is <n>/<seeds>.

Usage:
  uv run python scripts/update_docs.py --dry-run
  uv run python scripts/update_docs.py
  uv run python scripts/update_docs.py --config default --min-seeds 3
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
RESULTS = ROOT / "results"
DATA_JSON = ROOT / "docs" / "static" / "data.json"
INDEX_HTML = ROOT / "docs" / "index.html"

MONTH_KEYS = [f"2025-{m:02d}" for m in range(1, 13)]
LOGOS = "static/images/logos"

# Registry for the models in this sweep: slug -> leaderboard identity.
REGISTRY = {
    "x-ai/grok-4.5":                 ("grok-4.5",             "Grok 4.5",           "xAI",         "#4ade80", f"{LOGOS}/grok.png",              False),
    "x-ai/grok-4.6":                 ("grok-4.6",             "Grok 4.6",           "xAI",         "#16a34a", f"{LOGOS}/grok.png",              False),
    "qwen/qwen3.8-max":              ("qwen3.8-max",          "Qwen 3.8 Max",       "Alibaba",     "#ea580c", f"{LOGOS}/Qwen_logo.svg.png",     True),
    "deepseek/deepseek-v4-pro-0813": ("deepseek-v4-pro-0813", "DeepSeek V4 Pro 0813","DeepSeek",   "#6d28d9", f"{LOGOS}/deepseek-ai-icon-logo-png_seeklogo-611473.png", True),
    "meta/muse-spark-1.1":           ("muse-spark-1.1",       "Muse Spark 1.1",     "Meta",        "#1d4ed8", None,                             False),
    "meta/muse-spark-1.2":           ("muse-spark-1.2",       "Muse Spark 1.2",     "Meta",        "#3b82f6", None,                             False),
    "moonshotai/kimi-k3":            ("kimi-k3",              "Kimi K3",            "Moonshot AI", "#14b8a6", f"{LOGOS}/moonshotlogo.jpeg",     True),
    "openai/gpt-5.6-sol":            ("gpt-5.6-sol",          "GPT-5.6 Sol",        "OpenAI",      "#10a37f", f"{LOGOS}/openai_logo_icon_248315.png", False),
    "openai/gpt-5.6-terra":          ("gpt-5.6-terra",        "GPT-5.6 Terra",      "OpenAI",      "#0d9488", f"{LOGOS}/openai_logo_icon_248315.png", False),
    "openai/gpt-5.6-luna":           ("gpt-5.6-luna",         "GPT-5.6 Luna",       "OpenAI",      "#38bdf8", f"{LOGOS}/openai_logo_icon_248315.png", False),
    "anthropic/claude-opus-5":       ("claude-opus-5",        "Claude Opus 5",      "Anthropic",   "#9f1239", f"{LOGOS}/claude-color.png",      False),
    "google/gemini-3.6-flash":       ("gemini-3.6-flash",     "Gemini 3.6 Flash",   "Google",      "#7c3aed", f"{LOGOS}/gemini-color.png",      False),
    "thinkingmachines/inkling":      ("inkling",              "Inkling",            "Thinking Machines", "#f59e0b", None,                       False),
}


# ---------------------------------------------------------------------------
# Results -> monthly series
# ---------------------------------------------------------------------------

def month_end_series(funds_points, bankrupt: bool):
    """Net worth at each month end; 0 from bankruptcy onward, negatives clamped."""
    if not funds_points:
        return None
    parsed = []
    for p in funds_points:
        t = datetime.fromisoformat(p["time"])
        parsed.append((t, int(p["funds_cents"])))
    parsed.sort(key=lambda x: x[0])

    out, went_bust = {}, False
    for key in MONTH_KEYS:
        y, m = int(key[:4]), int(key[5:])
        boundary = datetime(y + (m == 12), (m % 12) + 1, 1, tzinfo=parsed[0][0].tzinfo)
        prior = [c for t, c in parsed if t < boundary]
        cents = prior[-1] if prior else parsed[0][1]
        if cents < 0 or went_bust:
            went_bust = True          # bankruptcy is terminal: stays 0 thereafter
            out[key] = 0.0
        else:
            out[key] = round(cents / 100, 2)
    if bankrupt and not went_bust:
        # Terminal bankruptcy that the ledger never shows as negative: zero the tail.
        last = max((k for k in MONTH_KEYS if out[k] > 0), default=None)
        if last:
            for key in MONTH_KEYS[MONTH_KEYS.index(last) + 1:]:
                out[key] = 0.0
    return out


def collect(config: str):
    """slug -> {'series': [per-seed month dicts], 'bankrupt': n, 'seeds': [..]}"""
    pat = re.compile(rf"^yc_bench_result_{re.escape(config)}_(\d+)_(.+)\.json$")
    acc = defaultdict(lambda: {"series": [], "bankrupt": 0, "seeds": [], "errors": []})
    for path in sorted(RESULTS.glob(f"yc_bench_result_{config}_*.json")):
        m = pat.match(path.name)
        if not m:
            continue
        seed = int(m.group(1))
        try:
            d = json.loads(path.read_text())
        except Exception as exc:
            print(f"  ! unreadable {path.name}: {exc}")
            continue
        slug = d.get("model", "").replace("openrouter/", "")
        reason = d.get("terminal_reason")
        entry = acc[slug]
        if reason == "error":
            entry["errors"].append(seed)
            continue
        series = month_end_series((d.get("time_series") or {}).get("funds") or [],
                                  bankrupt=(reason == "bankruptcy"))
        if series is None:
            print(f"  ! no funds series in {path.name}")
            continue
        entry["series"].append(series)
        entry["seeds"].append(seed)
        if reason == "bankruptcy":
            entry["bankrupt"] += 1
    return acc


def average(entry):
    n = len(entry["series"])
    return {k: round(sum(s[k] for s in entry["series"]) / n, 2) for k in MONTH_KEYS}


# ---------------------------------------------------------------------------
# docs/index.html rewriting
# ---------------------------------------------------------------------------

ROW_RE = re.compile(
    r'<span class="model-name"[^>]*>([^<]+)</span>'
    r'<span class="model-provider">([^<]+)</span>.*?'
    r'<span class="funds-value[^"]*">\$([\d,]+)</span>.*?'
    r'<span class="bankrupt-\w+">(\d+)/(\d+)</span>',
    re.S)


def parse_existing_rows(html: str):
    body = html[html.index('<tbody>'):html.index('</tbody>')]
    rows = {}
    for name, provider, worth, bust, total in ROW_RE.findall(body):
        rows[name.strip()] = {
            "provider": provider.strip(),
            "worth": float(worth.replace(",", "")),
            "bankrupt": int(bust),
            "seeds": int(total),
        }
    return rows


def render_tbody(rows: dict) -> str:
    """rows: name -> {provider, worth, bankrupt, seeds}. Greedy Bot pinned last."""
    ranked = sorted((r for n, r in rows.items() if n != "Greedy Bot"),
                    key=lambda r: -r["worth"])
    name_of = {id(r): n for n, r in rows.items()}
    out = ["          <tbody>"]
    for i, r in enumerate(ranked, 1):
        name = name_of[id(r)]
        tr = '<tr class="rank-1">' if i == 1 else "<tr>"
        rank = (f'<span class="rank-num gold">1</span><span class="rank-star">&#9733;</span>'
                if i == 1 else f'<span class="rank-num">{i}</span>')
        cls = "bankrupt-zero" if r["bankrupt"] == 0 else "bankrupt-some"
        neg = " negative" if r["worth"] <= 0 else ""
        out.append(
            f'            {tr}<td>{rank}</td><td><div class="model-cell">'
            f'<span class="model-name">{name}</span>'
            f'<span class="model-provider">{r["provider"]}</span></div></td>'
            f'<td><span class="funds-value{neg}">${r["worth"]:,.0f}</span></td>'
            f'<td><span class="{cls}">{r["bankrupt"]}/{r["seeds"]}</span></td></tr>')
    if "Greedy Bot" in rows:
        g = rows["Greedy Bot"]
        cls = "bankrupt-zero" if g["bankrupt"] == 0 else "bankrupt-some"
        out.append(
            f'            <tr><td><span class="rank-num" style="color:#ccc">-</span></td>'
            f'<td><div class="model-cell"><span class="model-name" '
            f'style="color:var(--text-muted)">Greedy Bot</span>'
            f'<span class="model-provider">{g["provider"]}</span></div></td>'
            f'<td><span class="funds-value negative">${g["worth"]:,.0f}</span></td>'
            f'<td><span class="{cls}">{g["bankrupt"]}/{g["seeds"]}</span></td></tr>')
    out.append("          </tbody>")
    return "\n".join(out)


def js_list(name: str, keys: list[str], html: str, per_line: int = 8) -> str:
    """Rewrite `const NAME = [ ... ];` preserving style."""
    start = html.index(f"const {name} = [")
    end = html.index("];", start) + 2
    lines, chunk = [], []
    for k in keys:
        chunk.append(f"'{k}'")
        if len(chunk) == per_line:
            lines.append("  " + ",".join(chunk) + ",")
            chunk = []
    if chunk:
        lines.append("  " + ",".join(chunk))
    return html[:start] + f"const {name} = [\n" + "\n".join(lines) + "\n];" + html[end:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="default")
    ap.add_argument("--min-seeds", type=int, default=1,
                    help="Skip models with fewer completed seeds than this")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    acc = collect(args.config)
    if not acc:
        print(f"No results found for config '{args.config}' in {RESULTS}/")
        return 1

    data = json.loads(DATA_JSON.read_text())
    html = INDEX_HTML.read_text()
    rows = parse_existing_rows(html)
    print(f"Existing leaderboard: {len(rows)} entries")

    added, skipped = [], []
    for slug, entry in sorted(acc.items()):
        n = len(entry["series"])
        if entry["errors"]:
            print(f"  ! {slug}: seeds {entry['errors']} errored (excluded)")
        if slug not in REGISTRY:
            print(f"  ! {slug}: not in REGISTRY — add an entry to include it")
            continue
        key, name, provider, color, logo, is_open = REGISTRY[slug]
        if n < args.min_seeds:
            skipped.append(f"{name} ({n}/{args.min_seeds} seeds)")
            continue
        avg = average(entry)
        data[key] = avg
        rows[name] = {"provider": provider, "worth": avg[MONTH_KEYS[-1]],
                      "bankrupt": entry["bankrupt"], "seeds": n}
        added.append((key, name, color, logo, is_open, avg[MONTH_KEYS[-1]],
                      entry["bankrupt"], n, sorted(entry["seeds"])))

    if skipped:
        print("  skipped (incomplete): " + ", ".join(skipped))
    if not added:
        print("Nothing to add.")
        return 0

    print(f"\n{'model':24}{'net worth':>14}{'bankrupt':>10}  seeds")
    for key, name, *_rest, worth, bust, n, seeds in added:
        print(f"  {name:22}{worth:>14,.0f}{f'{bust}/{n}':>10}  {seeds}")

    # MODEL_CONFIG entries
    for key, name, color, logo, is_open, *_ in added:
        if f"'{key}':" in html:
            continue
        logo_js = f"'{logo}'" if logo else "null"
        anchor = "const MODEL_CONFIG = {\n"
        entry = (f"  '{key}':".ljust(35)
                 + f" {{ name: '{name}',".ljust(34)
                 + f" color: '{color}', width: 2, logo: {logo_js} }},\n")
        html = html.replace(anchor, anchor + entry, 1)

    # SORT_ORDER: all models by final net worth desc, greedy_bot last
    key_by_name = {n: k for k, n, *_ in added}
    order_src = []
    m = re.search(r"const MODEL_CONFIG = \{(.*?)\n\};", html, re.S)
    cfg_keys = re.findall(r"^\s*'([^']+)':", m.group(1), re.M)
    name_to_key = {}
    for ck in cfg_keys:
        nm = re.search(rf"'{re.escape(ck)}':\s*\{{ name: '([^']+)'", html)
        if nm:
            name_to_key[nm.group(1)] = ck
    ranked_names = sorted((n for n in rows if n != "Greedy Bot"),
                          key=lambda n: -rows[n]["worth"])
    for n in ranked_names:
        k = name_to_key.get(n) or key_by_name.get(n)
        if not k:
            print(f"  ! '{n}' has no MODEL_CONFIG key — omitted from the chart")
        elif k not in data:
            print(f"  ! MODEL_CONFIG key '{k}' ({n}) has no data.json entry — "
                  f"it cannot render on the chart; keys must match")
        else:
            order_src.append(k)
    if "greedy_bot" in data:
        order_src.append("greedy_bot")
    html = js_list("SORT_ORDER", order_src, html)

    # OPEN_SOURCE additions
    open_new = [k for k, _n, _c, _l, is_open, *_ in added if is_open]
    if open_new:
        mo = re.search(r"const OPEN_SOURCE = new Set\(\[(.*?)\]\);", html, re.S)
        cur = re.findall(r"'([^']+)'", mo.group(1))
        merged = cur + [k for k in open_new if k not in cur]
        block = ",".join(f"'{k}'" for k in merged)
        wrapped, line = [], "  "
        for tok in block.split(","):
            if len(line) + len(tok) > 100:
                wrapped.append(line.rstrip()); line = "  "
            line += tok + ","
        wrapped.append(line.rstrip(","))
        html = (html[:mo.start()] + "const OPEN_SOURCE = new Set([\n"
                + "\n".join(wrapped) + "\n]);" + html[mo.end():])

    # Leaderboard table
    body_start = html.index("          <tbody>")
    body_end = html.index("</tbody>") + len("</tbody>")
    html = html[:body_start] + render_tbody(rows) + html[body_end:]

    html = re.sub(r"Average net worth across \d+ seeds",
                  f"Average net worth across {max(a[-2] for a in added)} seeds", html)

    if args.dry_run:
        print("\n--dry-run: no files written")
        print(f"  would write {len(data)} models to {DATA_JSON.relative_to(ROOT)}")
        print(f"  would rewrite tbody with {len(rows)} rows in {INDEX_HTML.relative_to(ROOT)}")
        return 0

    DATA_JSON.write_text(json.dumps(data, indent=2) + "\n")
    INDEX_HTML.write_text(html)
    print(f"\nWrote {DATA_JSON.relative_to(ROOT)} ({len(data)} models)")
    print(f"Wrote {INDEX_HTML.relative_to(ROOT)} ({len(rows)} table rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
