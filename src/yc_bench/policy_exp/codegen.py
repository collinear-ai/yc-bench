"""Generate a deterministic policy by prompting a target LLM once.

The model is asked to emit a Python code block defining `run(env)`. We
extract the code, validate that it parses and exposes `run`, and write
it to `policies/<model_slug>.py` for archival and re-runs.
"""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import litellm

from .prompts import build_codegen_messages

logger = logging.getLogger(__name__)


_CODE_FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)
_OPEN_FENCE = re.compile(r"```(?:python|py)?\s*\n(.*)\Z", re.DOTALL)


@dataclass
class GeneratedPolicy:
    model: str
    code: str
    path: Path
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


_RUN_DEF = re.compile(r"^\s*def\s+run\s*\(", re.MULTILINE)


def _extract_code(text: str) -> str:
    """Pull the policy code out of the model's response.

    Models often return multiple fenced blocks interleaved with prose
    (especially reasoning-style outputs). We:
      1. Prefer a block that defines `def run(env)`.
      2. Else concatenate all fenced blocks in order (handles models that
         split a single program across blocks with commentary).
      3. Else fall back to the raw text.
    """
    matches = [m.strip() for m in _CODE_FENCE.findall(text)]
    if matches:
        runs = [m for m in matches if _RUN_DEF.search(m)]
        if runs:
            return runs[-1]
        joined = "\n\n".join(matches)
        if _RUN_DEF.search(joined):
            return joined
        # Otherwise fall through and try unclosed-fence extraction below;
        # the closed blocks were just incomplete sketches.

    open_match = _OPEN_FENCE.search(text)
    if open_match:
        candidate = open_match.group(1).strip()
        if _RUN_DEF.search(candidate):
            return candidate

    if matches:
        return "\n\n".join(matches)
    return text.strip()


def _validate(code: str) -> None:
    """Ensure the code parses and defines a top-level `run` function."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ValueError(f"generated policy has SyntaxError: {exc}") from exc

    has_run = any(
        isinstance(node, ast.FunctionDef) and node.name == "run"
        for node in tree.body
    )
    if not has_run:
        raise ValueError("generated policy is missing top-level `def run(env): ...`")


def _slug(model: str) -> str:
    return model.replace("/", "_").replace(":", "_")


def generate_policy(
    model: str,
    out_dir: Path | str = "policies",
    *,
    extra_hint: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 16000,
    timeout: float = 600.0,
    overwrite: bool = True,
) -> GeneratedPolicy:
    """Ask `model` to write a policy. Save it under out_dir/<slug>.py."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_slug(model)}.py"

    if path.exists() and not overwrite:
        code = path.read_text()
        _validate(code)
        return GeneratedPolicy(
            model=model, code=code, path=path,
            prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
        )

    messages = build_codegen_messages(extra_hint=extra_hint)
    logger.info("Codegen: prompting %s for a policy.", model)

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
        debug_path = out_dir / f"{_slug(model)}.raw.txt"
        debug_path.write_text(content)
        logger.error(
            "Validation failed for %s. Raw response (%d chars) saved to %s",
            model, len(content), debug_path,
        )
        raise

    usage = getattr(resp, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
    completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
    cost = getattr(resp, "_hidden_params", {}).get("response_cost") or 0.0

    header = (
        f'"""Auto-generated policy from model={model}.\n'
        f'Do not edit by hand — re-run scripts/policy_experiment.py to refresh.\n'
        f'"""\n\n'
    )
    path.write_text(header + code + "\n")
    logger.info(
        "Codegen done: %s (%d→%d tokens, $%.4f) → %s",
        model, prompt_tokens, completion_tokens, cost, path,
    )

    return GeneratedPolicy(
        model=model,
        code=code,
        path=path,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=float(cost),
    )


def load_policy(path: Path | str):
    """Import a generated policy file and return its `run` callable."""
    import importlib.util

    path = Path(path)
    spec = importlib.util.spec_from_file_location(f"policy_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    if not hasattr(module, "run"):
        raise AttributeError(f"{path} does not define run(env)")
    return module.run


__all__ = ["GeneratedPolicy", "generate_policy", "load_policy"]
