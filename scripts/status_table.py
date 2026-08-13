"""Ending funds per (model x seed). Completed runs from results/, live runs from db/ transcripts.

  uv run python scripts/status_table.py [--config default]

Legend:  ok = reached horizon   BUST = bankrupt   .. = still running (% of sim year)
"""
from __future__ import annotations

import argparse, datetime, json, re
from pathlib import Path

ROOT = Path(__file__).parent.parent
START = datetime.date(2025, 1, 1)


def short(slug: str) -> str:
    return slug.replace("openrouter/", "").split("/", 1)[-1]


def scan(config: str):
    cells: dict[tuple[str, int], dict] = {}

    # Completed runs: authoritative final funds from the ledger series.
    for p in sorted((ROOT / "results").glob(f"yc_bench_result_{config}_*.json")):
        m = re.match(rf"^yc_bench_result_{re.escape(config)}_(\d+)_(.+)\.json$", p.name)
        if not m:
            continue
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        funds = (d.get("time_series") or {}).get("funds") or []
        reason = d.get("terminal_reason")
        cells[(short(d.get("model", m.group(2))), int(m.group(1)))] = {
            "funds": funds[-1]["funds_cents"] / 100 if funds else None,
            "state": {"horizon_end": "ok", "bankruptcy": "BUST"}.get(reason, "ERR"),
            "turns": d.get("turns_completed", 0),
            "pct": 100.0,
        }

    # Live runs: last line of the transcript wins unless a result already exists.
    for p in sorted((ROOT / "db").glob(f"{config}_*.transcript.jsonl")):
        m = re.match(rf"^{re.escape(config)}_(\d+)_(.+)\.transcript\.jsonl$", p.name)
        if not m:
            continue
        seed = int(m.group(1))
        slug = short(m.group(2).replace("openrouter_", "").replace("_", "/", 1))
        lines = [l for l in p.read_text(errors="ignore").splitlines() if l.strip()]
        if not lines:
            continue
        try:
            last = json.loads(lines[-1])
        except Exception:
            continue
        if (slug, seed) in cells and cells[(slug, seed)]["state"] != "ERR":
            continue
        t = (last.get("sim_time") or "")[:10]
        pct = None
        if re.match(r"\d{4}-\d{2}-\d{2}", t):
            pct = (datetime.date(*map(int, t.split("-"))) - START).days / 365 * 100
        cells[(slug, seed)] = {
            "funds": last.get("funds_cents", 0) / 100,
            "state": "..",
            "turns": len(lines),
            "pct": pct,
        }
    return cells


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="default")
    args = ap.parse_args()

    cells = scan(args.config)
    if not cells:
        print("No runs found yet.")
        return 0

    models = sorted({m for m, _ in cells})
    seeds = sorted({s for _, s in cells})
    w = max(len(m) for m in models) + 1
    col = 20

    print(f"\n  ending funds — config={args.config}   {datetime.datetime.now():%H:%M:%S}")
    print(f"  {'model':<{w}}" + "".join(f"{'seed ' + str(s):>{col}}" for s in seeds)
          + f"{'mean':>{col}}")
    print("  " + "-" * (w + col * (len(seeds) + 1)))

    for mdl in models:
        line = f"  {mdl:<{w}}"
        vals = []
        for s in seeds:
            c = cells.get((mdl, s))
            if c is None:
                line += f"{'—':>{col}}"
                continue
            f = c["funds"]
            vals.append(f)
            tag = c["state"] if c["state"] != ".." else f'..{c["pct"]:.0f}%' if c["pct"] is not None else ".."
            line += f"{f'${f:,.0f} {tag}':>{col}}"
        line += f"{f'${sum(vals)/len(vals):,.0f}' if vals else '—':>{col}}"
        print(line)

    done = sum(1 for c in cells.values() if c["state"] in ("ok", "BUST"))
    live = sum(1 for c in cells.values() if c["state"] == "..")
    print(f"\n  {done} complete, {live} running   (ok=horizon  BUST=bankrupt  ..=in progress)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
