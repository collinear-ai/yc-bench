"""Hill-climbing loop: re-prompt the same model with its own prior
results and policy code, expecting it to converge on something that
actually drives the simulation.

Each iteration writes:
    policies/<slug>_iter<N>.py
    results/iterate_<config>_<seed>_<slug>_iter<N>.json

A combined trajectory is written to:
    results/iterate_history_<config>_<seed>_<slug>.json
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import litellm

from .codegen import _extract_code, _slug, _validate
from .prompts import build_codegen_messages, build_iterate_messages
from .runner import EpisodeResult, run_policy_episode

logger = logging.getLogger(__name__)


@dataclass
class IterationRecord:
    iteration: int
    policy_path: str
    codegen_prompt_tokens: int
    codegen_completion_tokens: int
    codegen_cost_usd: float
    final_funds_cents: int
    funds_delta_cents: int
    bankrupt: bool
    terminal_reason: str | None
    sim_commands: int
    helper_calls: int
    wall_seconds: float
    error: str | None
    task_status_counts: dict[str, int] = field(default_factory=dict)
    ledger_entries: int = 0
    error_codes: dict[str, int] = field(default_factory=dict)
    last_commands: list[str] = field(default_factory=list)
    # Richer diagnostic fields (added so models can see WHY they failed,
    # not just THAT they failed).
    monthly_timeline: list[dict] = field(default_factory=list)
    task_outcomes: dict = field(default_factory=dict)
    payroll_growth: dict = field(default_factory=dict)
    concurrency: dict = field(default_factory=dict)


def _monthly_timeline(ledger: list[dict], initial_funds_cents: int) -> list[dict]:
    """Bucket ledger entries by sim-month. Reveals when revenue stopped
    keeping up with payroll, which is the root cause of most bankruptcies."""
    from datetime import datetime

    bins: dict[str, dict] = {}
    for e in ledger:
        try:
            t = datetime.fromisoformat(e["time"].replace("Z", "+00:00"))
        except Exception:
            continue
        key = f"{t.year}-{t.month:02d}"
        bucket = bins.setdefault(key, {
            "month": key,
            "revenue_cents": 0,
            "payroll_cents": 0,
            "penalties_cents": 0,
            "other_cents": 0,
            "events": 0,
        })
        cat = (e.get("category") or "").lower()
        amt = int(e.get("amount_cents", 0))
        bucket["events"] += 1
        if "payroll" in cat or "salary" in cat:
            bucket["payroll_cents"] += amt  # negative
        elif amt > 0:
            bucket["revenue_cents"] += amt
        elif "penalty" in cat or "dispute" in cat or "clawback" in cat:
            bucket["penalties_cents"] += amt
        else:
            bucket["other_cents"] += amt

    # Walk months in order, carrying running balance.
    out: list[dict] = []
    running = initial_funds_cents
    for key in sorted(bins.keys()):
        b = bins[key]
        net = (
            b["revenue_cents"] + b["payroll_cents"]
            + b["penalties_cents"] + b["other_cents"]
        )
        b["funds_at_start_cents"] = running
        b["net_delta_cents"] = net
        running += net
        b["funds_at_end_cents"] = running
        out.append(b)
    return out


def _task_outcomes(tasks: list[dict]) -> dict:
    """Aggregate task lifecycle outcomes — how many accepted, completed,
    failed deadlines, canceled, and total revenue/penalty captured."""
    counts: Counter = Counter()
    success_reward = 0
    fail_advertised = 0
    sample_failed = []
    for t in tasks:
        status = t.get("status", "?")
        counts[status] += 1
        if status == "completed_success":
            success_reward += int(t.get("reward_funds_cents", 0) or 0)
        elif status == "completed_fail":
            fail_advertised += int(t.get("advertised_reward_cents", 0) or 0)
            if len(sample_failed) < 3:
                sample_failed.append({
                    "title": t.get("title"),
                    "client_name": t.get("client_name"),
                    "advertised_reward_cents": int(t.get("advertised_reward_cents", 0) or 0),
                })
    return {
        "by_status": dict(counts),
        "completed_success_revenue_cents": success_reward,
        "completed_fail_advertised_total_cents": fail_advertised,
        "sample_failed_tasks": sample_failed,
    }


def _payroll_growth(ledger: list[dict], employees: list[dict]) -> dict:
    """Initial vs final monthly payroll. Compounds via salary bumps —
    if final is much higher than initial, the policy assigned too many
    employees per task."""
    payroll_entries = [e for e in ledger if "payroll" in (e.get("category") or "").lower()]
    initial = abs(int(payroll_entries[0]["amount_cents"])) if payroll_entries else None
    # Final payroll = sum of current employee salaries (ground truth)
    final = sum(int(e.get("salary_cents", 0) or 0) for e in employees) if employees else None
    growth = (final / initial) if (initial and final) else None
    return {
        "initial_monthly_cents": initial,
        "final_monthly_cents": final,
        "growth_factor": round(growth, 2) if growth else None,
        "n_payroll_events": len(payroll_entries),
    }


def _concurrency(tasks: list[dict]) -> dict:
    """Max and mean concurrent active tasks. Too few = idle employees;
    too many = throughput-split-rate division dilution."""
    from datetime import datetime

    intervals = []
    for t in tasks:
        a = t.get("accepted_at")
        c = t.get("completed_at")
        if not a:
            continue
        try:
            start = datetime.fromisoformat(a.replace("Z", "+00:00"))
        except Exception:
            continue
        end = None
        if c:
            try:
                end = datetime.fromisoformat(c.replace("Z", "+00:00"))
            except Exception:
                pass
        intervals.append((start, end))
    if not intervals:
        return {"max_active": 0, "mean_active": 0.0}

    # Sweep events
    events = []
    horizon_end = max((e for _, e in intervals if e), default=None)
    for s, e in intervals:
        events.append((s, +1))
        if e:
            events.append((e, -1))
        elif horizon_end:
            events.append((horizon_end, -1))
    events.sort()

    cur = 0
    peak = 0
    weighted = 0.0
    total_seconds = 0.0
    last_t = None
    for t, delta in events:
        if last_t is not None:
            dt = (t - last_t).total_seconds()
            weighted += cur * dt
            total_seconds += dt
        cur += delta
        peak = max(peak, cur)
        last_t = t
    mean = (weighted / total_seconds) if total_seconds > 0 else 0.0
    return {"max_active": peak, "mean_active": round(mean, 2)}


def _summarize_episode(result: EpisodeResult) -> dict:
    """Distill an episode into the JSON we feed back into the next prompt.

    The fields here become diagnostic context for the next codegen call.
    The bias is toward *why* something failed (monthly cashflow, task
    outcomes, payroll growth, concurrency) rather than just *that* it
    failed. Hidden info (e.g. client.is_rat / loyalty) is intentionally
    NOT exposed — the model has to infer adversarial clients."""
    ts = result.time_series or {}
    tasks = ts.get("tasks", [])
    ledger = ts.get("ledger", [])
    employees = ts.get("employees", [])

    statuses = Counter(t.get("status", "?") for t in tasks)
    err_codes: Counter = Counter()
    for c in result.command_log_tail:
        if not c.get("ok"):
            m = re.match(r"yc-bench (\S+)(?:\s+(\S+))?", c.get("command", ""))
            if m:
                err_codes[f"{m.group(1)} {m.group(2) or ''}".strip()] += 1
    last_cmds = [c.get("command", "") for c in result.command_log_tail[-15:]]

    return {
        "final_funds_cents": result.final_funds_cents,
        "funds_delta_cents": result.funds_delta_cents,
        "bankrupt": result.bankrupt,
        "terminal_reason": result.terminal_reason,
        "sim_commands": result.sim_commands,
        "helper_calls": result.helper_calls,
        "wall_seconds": result.wall_seconds,
        "error": result.error,
        "task_status_counts": dict(statuses),
        "ledger_entries": len(ledger),
        "error_codes": dict(err_codes),
        "last_commands": last_cmds,
        "monthly_timeline": _monthly_timeline(ledger, result.initial_funds_cents),
        "task_outcomes": _task_outcomes(tasks),
        "payroll_growth": _payroll_growth(ledger, employees),
        "concurrency": _concurrency(tasks),
    }


def _generate_initial(
    model: str,
    out_path: Path,
    *,
    max_tokens: int,
    temperature: float,
    timeout: float,
) -> tuple[str, dict]:
    """Initial codegen — same as codegen.generate_policy but inline so
    we can write to a per-iteration filename."""
    messages = build_codegen_messages()
    resp = litellm.completion(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    content = resp.choices[0].message.content or ""
    code = _extract_code(content)
    try:
        _validate(code)
    except ValueError:
        out_path.with_suffix(".raw.txt").write_text(content)
        raise
    out_path.write_text(_header(model, 0) + code + "\n")
    return code, _usage(resp)


def _generate_revision(
    model: str,
    history: list[dict],
    prev_code: str,
    out_path: Path,
    iteration: int,
    *,
    max_tokens: int,
    temperature: float,
    timeout: float,
) -> tuple[str, dict]:
    messages = build_iterate_messages(history, prev_code)
    resp = litellm.completion(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    content = resp.choices[0].message.content or ""
    code = _extract_code(content)
    try:
        _validate(code)
    except ValueError:
        out_path.with_suffix(".raw.txt").write_text(content)
        raise
    out_path.write_text(_header(model, iteration) + code + "\n")
    return code, _usage(resp)


def _header(model: str, iteration: int) -> str:
    return (
        f'"""Auto-generated policy. model={model} iteration={iteration}.\n'
        f'Do not edit by hand.\n'
        f'"""\n\n'
    )


def _usage(resp) -> dict:
    usage = getattr(resp, "usage", None)
    return {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "cost_usd": float(getattr(resp, "_hidden_params", {}).get("response_cost") or 0.0),
    }


def iterate_model(
    model: str,
    n_iterations: int,
    *,
    seed: int = 1,
    config_name: str = "default",
    helper_model: str | None = None,
    wall_timeout_seconds: float = 600.0,
    max_commands: int = 5000,
    max_helper_calls: int = 200,
    max_tokens: int = 16000,
    temperature: float = 0.0,
    codegen_timeout: float = 600.0,
    policies_dir: Path | str = "policies",
    results_dir: Path | str = "results",
    history_window: int = 5,
) -> list[IterationRecord]:
    """Hill-climb a single model for n_iterations on a fixed seed.

    Returns one IterationRecord per iteration. After each iteration, the
    model sees a JSON summary of all prior iterations (most recent last,
    truncated to history_window for context-window safety) plus its own
    most recent policy source.
    """
    policies_dir = Path(policies_dir)
    results_dir = Path(results_dir)
    policies_dir.mkdir(exist_ok=True)
    results_dir.mkdir(exist_ok=True)
    slug = _slug(model)

    history_path = results_dir / f"iterate_history_{config_name}_{seed}_{slug}.json"
    records: list[IterationRecord] = []
    history_for_prompt: list[dict] = []
    prev_code: str | None = None

    for i in range(1, n_iterations + 1):
        policy_path = policies_dir / f"{slug}_iter{i}.py"
        logger.info("=== %s iteration %d/%d ===", model, i, n_iterations)

        # --- Codegen ---
        try:
            if i == 1:
                code, usage = _generate_initial(
                    model, policy_path,
                    max_tokens=max_tokens, temperature=temperature, timeout=codegen_timeout,
                )
            else:
                trimmed = history_for_prompt[-history_window:]
                code, usage = _generate_revision(
                    model, trimmed, prev_code or "", policy_path, i,
                    max_tokens=max_tokens, temperature=temperature, timeout=codegen_timeout,
                )
        except Exception as exc:
            logger.error("Codegen failed at iter %d for %s: %s", i, model, exc)
            rec = IterationRecord(
                iteration=i,
                policy_path=str(policy_path),
                codegen_prompt_tokens=0,
                codegen_completion_tokens=0,
                codegen_cost_usd=0.0,
                final_funds_cents=0,
                funds_delta_cents=0,
                bankrupt=False,
                terminal_reason=None,
                sim_commands=0,
                helper_calls=0,
                wall_seconds=0.0,
                error=f"codegen: {type(exc).__name__}: {exc}",
            )
            records.append(rec)
            history_for_prompt.append({"iteration": i, **_summary_from_record(rec)})
            _flush_history(history_path, records)
            continue

        prev_code = code
        logger.info(
            "Codegen iter %d: %d→%d tokens, $%.4f → %s",
            i, usage["prompt_tokens"], usage["completion_tokens"], usage["cost_usd"], policy_path,
        )

        # --- Episode ---
        try:
            ep = run_policy_episode(
                model=model,
                seed=seed,
                policy_path=policy_path,
                config_name=config_name,
                helper_model=helper_model,
                max_commands=max_commands,
                max_helper_calls=max_helper_calls,
                wall_timeout_seconds=wall_timeout_seconds,
            )
        except Exception as exc:
            logger.exception("Episode crash at iter %d for %s", i, model)
            rec = IterationRecord(
                iteration=i,
                policy_path=str(policy_path),
                codegen_prompt_tokens=usage["prompt_tokens"],
                codegen_completion_tokens=usage["completion_tokens"],
                codegen_cost_usd=usage["cost_usd"],
                final_funds_cents=0,
                funds_delta_cents=0,
                bankrupt=False,
                terminal_reason=None,
                sim_commands=0,
                helper_calls=0,
                wall_seconds=0.0,
                error=f"episode crash: {type(exc).__name__}: {exc}",
            )
            records.append(rec)
            history_for_prompt.append({"iteration": i, **_summary_from_record(rec)})
            _flush_history(history_path, records)
            continue

        summary = _summarize_episode(ep)
        rec = IterationRecord(
            iteration=i,
            policy_path=str(policy_path),
            codegen_prompt_tokens=usage["prompt_tokens"],
            codegen_completion_tokens=usage["completion_tokens"],
            codegen_cost_usd=usage["cost_usd"],
            final_funds_cents=summary["final_funds_cents"],
            funds_delta_cents=summary["funds_delta_cents"],
            bankrupt=summary["bankrupt"],
            terminal_reason=summary["terminal_reason"],
            sim_commands=summary["sim_commands"],
            helper_calls=summary["helper_calls"],
            wall_seconds=summary["wall_seconds"],
            error=summary["error"],
            task_status_counts=summary["task_status_counts"],
            ledger_entries=summary["ledger_entries"],
            error_codes=summary["error_codes"],
            last_commands=summary["last_commands"],
            monthly_timeline=summary["monthly_timeline"],
            task_outcomes=summary["task_outcomes"],
            payroll_growth=summary["payroll_growth"],
            concurrency=summary["concurrency"],
        )
        records.append(rec)
        history_for_prompt.append({"iteration": i, **summary})

        # Persist this iteration's full episode JSON for post-hoc analysis.
        ep_path = results_dir / f"iterate_{config_name}_{seed}_{slug}_iter{i}.json"
        ep_dict = {
            "iteration": i,
            "model": model,
            "seed": seed,
            "config_name": config_name,
            "policy_path": str(policy_path),
            "codegen_usage": usage,
            "episode": ep.to_dict(),
        }
        ep_path.write_text(json.dumps(ep_dict, indent=2, default=str))

        _flush_history(history_path, records)

        logger.info(
            "Iter %d done: funds=$%.2f bankrupt=%s reason=%s cmds=%d wall=%ss",
            i,
            ep.final_funds_cents / 100,
            ep.bankrupt,
            ep.terminal_reason,
            ep.sim_commands,
            ep.wall_seconds,
        )

    return records


def _summary_from_record(rec: IterationRecord) -> dict:
    """Subset of IterationRecord fields we feed back into the prompt."""
    return {
        "final_funds_cents": rec.final_funds_cents,
        "funds_delta_cents": rec.funds_delta_cents,
        "bankrupt": rec.bankrupt,
        "terminal_reason": rec.terminal_reason,
        "sim_commands": rec.sim_commands,
        "helper_calls": rec.helper_calls,
        "wall_seconds": rec.wall_seconds,
        "error": rec.error,
        "task_status_counts": rec.task_status_counts,
        "ledger_entries": rec.ledger_entries,
        "error_codes": rec.error_codes,
        "last_commands": rec.last_commands,
        "monthly_timeline": rec.monthly_timeline,
        "task_outcomes": rec.task_outcomes,
        "payroll_growth": rec.payroll_growth,
        "concurrency": rec.concurrency,
    }


def _flush_history(path: Path, records: list[IterationRecord]) -> None:
    path.write_text(json.dumps([asdict(r) for r in records], indent=2, default=str))


__all__ = ["IterationRecord", "iterate_model"]
