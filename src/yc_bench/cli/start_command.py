"""Interactive 3-step quickstart for YC-Bench."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt, IntPrompt
from rich.table import Table

console = Console()

# ── Model catalogue (Feb 2026) ───────────────────────────────────────────

MODELS: list[dict] = [
    # ── Anthropic ──
    {"provider": "Anthropic", "name": "Claude Opus 4.6",   "id": "anthropic/claude-opus-4-6",            "key_env": "ANTHROPIC_API_KEY"},
    {"provider": "Anthropic", "name": "Claude Sonnet 4.6", "id": "anthropic/claude-sonnet-4-6",          "key_env": "ANTHROPIC_API_KEY"},
    {"provider": "Anthropic", "name": "Claude Haiku 4.5",  "id": "anthropic/claude-haiku-4-5-20251001",  "key_env": "ANTHROPIC_API_KEY"},
    # ── OpenAI ──
    {"provider": "OpenAI", "name": "GPT-5.2",       "id": "openai/gpt-5.2",       "key_env": "OPENAI_API_KEY"},
    {"provider": "OpenAI", "name": "GPT-5.1 Mini",  "id": "openai/gpt-5.1-mini",  "key_env": "OPENAI_API_KEY"},
    {"provider": "OpenAI", "name": "GPT-4.1",       "id": "openai/gpt-4.1",       "key_env": "OPENAI_API_KEY"},
    {"provider": "OpenAI", "name": "o4-mini",        "id": "openai/o4-mini",        "key_env": "OPENAI_API_KEY"},
    # ── Google (via OpenRouter) ──
    {"provider": "Google", "name": "Gemini 3.1 Pro",    "id": "openrouter/google/gemini-3.1-pro-preview",    "key_env": "OPENROUTER_API_KEY"},
    {"provider": "Google", "name": "Gemini 3 Flash",    "id": "openrouter/google/gemini-3-flash-preview",    "key_env": "OPENROUTER_API_KEY"},
    {"provider": "Google", "name": "Gemini 2.5 Flash (free)", "id": "openrouter/google/gemini-2.5-flash-preview:free", "key_env": "OPENROUTER_API_KEY"},
    # ── DeepSeek (via OpenRouter) ──
    {"provider": "DeepSeek", "name": "DeepSeek V3",       "id": "openrouter/deepseek/deepseek-chat",     "key_env": "OPENROUTER_API_KEY"},
    {"provider": "DeepSeek", "name": "DeepSeek R1",       "id": "openrouter/deepseek/deepseek-reasoner", "key_env": "OPENROUTER_API_KEY"},
    # ── xAI (via OpenRouter) ──
    {"provider": "xAI", "name": "Grok 3 Mini",  "id": "openrouter/x-ai/grok-3-mini-fast",  "key_env": "OPENROUTER_API_KEY"},
    # ── Qwen (via OpenRouter) ──
    {"provider": "Qwen", "name": "Qwen3 235B",       "id": "openrouter/qwen/qwen3-235b-a22b",       "key_env": "OPENROUTER_API_KEY"},
    {"provider": "Qwen", "name": "Qwen3 30B (free)", "id": "openrouter/qwen/qwen3-30b-a3b:free",     "key_env": "OPENROUTER_API_KEY"},
    # ── Meta (via OpenRouter) ──
    {"provider": "Meta", "name": "Llama 4 Scout",  "id": "openrouter/meta-llama/llama-4-scout",          "key_env": "OPENROUTER_API_KEY"},
    {"provider": "Meta", "name": "Llama 3.3 70B",  "id": "openrouter/meta-llama/llama-3.3-70b-instruct", "key_env": "OPENROUTER_API_KEY"},
    # ── Mistral (via OpenRouter) ──
    {"provider": "Mistral", "name": "Mistral Medium 3",  "id": "openrouter/mistralai/mistral-medium-3",  "key_env": "OPENROUTER_API_KEY"},
]


# ── API key detection ────────────────────────────────────────────────────

KEY_PATTERNS: list[tuple[str, str, str]] = [
    # (prefix, env_var_name, provider_label)  — order matters
    ("sk-ant-",  "ANTHROPIC_API_KEY",   "Anthropic"),
    ("sk-or-",   "OPENROUTER_API_KEY",  "OpenRouter"),
    ("AIza",     "GEMINI_API_KEY",      "Google Gemini"),
    ("sk-",      "OPENAI_API_KEY",      "OpenAI"),
]


def detect_key(api_key: str) -> tuple[str, str]:
    """Return (env_var_name, provider_label) based on key prefix."""
    for prefix, env_var, label in KEY_PATTERNS:
        if api_key.startswith(prefix):
            return env_var, label
    return "OPENROUTER_API_KEY", "Unknown (set as OpenRouter)"


# ── Config presets ───────────────────────────────────────────────────────

PRESETS = [
    ("tutorial",  "Tutorial",   "1 yr", "10 emp", "200 tasks", "Learn the basics"),
    ("easy",      "Easy",       "1 yr", "10 emp", "200 tasks", "Gentle intro"),
    ("medium",    "Medium",     "1 yr", "10 emp", "200 tasks", "Prestige + specialization"),
    ("hard",      "Hard",       "1 yr", "10 emp", "200 tasks", "Deadline pressure"),
    ("nightmare", "Nightmare",  "1 yr", "10 emp", "200 tasks", "Sustained perfection"),
]


def _resolve_api_key(needed_env: str | None, provider_label: str | None) -> tuple[str, str, str]:
    """Try env, then .env file, then prompt. Returns (api_key, env_var, label)."""
    # 1. Already in os.environ?
    if needed_env:
        val = os.environ.get(needed_env)
        if val:
            masked = val[:8] + "..." + val[-4:]
            console.print(f"  Found [cyan]{needed_env}[/cyan] in environment: [dim]{masked}[/dim]")
            if Confirm.ask("  Use this key?", default=True):
                return val, needed_env, provider_label or "detected"

    # 2. In .env?
    from dotenv import find_dotenv, load_dotenv
    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path and needed_env:
        load_dotenv(dotenv_path, override=False)
        val = os.environ.get(needed_env)
        if val:
            masked = val[:8] + "..." + val[-4:]
            console.print(f"  Found [cyan]{needed_env}[/cyan] in .env: [dim]{masked}[/dim]")
            if Confirm.ask("  Use this key?", default=True):
                return val, needed_env, provider_label or "detected"

    # 3. Ask
    api_key = Prompt.ask("  Paste your API key", password=True)
    env_var, label = detect_key(api_key)
    return api_key, env_var, label


def _build_custom_preset() -> str:
    """Interactively build a custom preset TOML. Returns path to temp file."""
    console.print("  [dim]Build your own config (press Enter for defaults)[/dim]\n")

    base = Prompt.ask("  Base preset to extend", choices=[p[0] for p in PRESETS], default="medium")
    horizon = IntPrompt.ask("  Horizon (years)", default=1)
    employees = IntPrompt.ask("  Number of employees", default=5)
    tasks = IntPrompt.ask("  Market tasks", default=150)
    max_turns = IntPrompt.ask("  Max turns", default=500)

    toml_content = (
        f'extends = "{base}"\n'
        f'name = "custom"\n'
        f'description = "Custom preset"\n\n'
        f'[sim]\nhorizon_years = {horizon}\n\n'
        f'[loop]\nmax_turns = {max_turns}\n\n'
        f'[world]\nnum_employees = {employees}\n'
        f'num_market_tasks = {tasks}\n'
    )

    console.print()
    console.print(Panel(toml_content.strip(), title="Your config", border_style="dim"))

    fd, path = tempfile.mkstemp(suffix=".toml", prefix="yc_bench_custom_")
    with os.fdopen(fd, "w") as f:
        f.write(toml_content)

    return path


# ── Main flow ────────────────────────────────────────────────────────────

def start_interactive():
    console.print()
    console.print(Panel.fit(
        "[bold cyan]YC-Bench Quickstart[/bold cyan]\n"
        "Evaluate any LLM as a startup CEO in 3 steps",
        border_style="cyan",
    ))
    console.print()

    # ━━ Step 1: Config ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    console.print("[bold yellow]Step 1/3[/bold yellow]  [bold]Configure the eval[/bold]\n")

    diff_table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    diff_table.add_column("#", style="dim", width=4)
    diff_table.add_column("Preset", width=14)
    diff_table.add_column("Horizon", width=8)
    diff_table.add_column("Team", width=8)
    diff_table.add_column("Tasks", width=10)
    diff_table.add_column("Description", style="dim")

    for i, (key, name, horizon, emp, tasks, desc) in enumerate(PRESETS, 1):
        style = "bold" if key == "medium" else ""
        rec = " (recommended)" if key == "medium" else ""
        diff_table.add_row(str(i), f"{name}{rec}", horizon, emp, tasks, desc, style=style)

    diff_table.add_row("", "", "", "", "", "")
    diff_table.add_row("0", "[italic]Custom[/italic]", "", "", "", "Build your own config")
    console.print(diff_table)
    console.print()

    diff_choice = IntPrompt.ask("Enter number", default=3)

    if diff_choice == 0:
        config_key = _build_custom_preset()
        config_display = "custom"
    elif 1 <= diff_choice <= len(PRESETS):
        config_key = PRESETS[diff_choice - 1][0]
        config_display = PRESETS[diff_choice - 1][1]
    else:
        console.print("[red]Invalid choice[/red]")
        raise typer.Exit(1)

    console.print(f"  [green]>[/green] {config_display}\n")

    seed = IntPrompt.ask("  Seed", default=1)
    console.print()

    # ━━ Step 2: Model ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    console.print("[bold yellow]Step 2/3[/bold yellow]  [bold]Choose a model[/bold]\n")

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("#", style="dim", width=4)
    table.add_column("Provider", style="cyan", width=12)
    table.add_column("Model", width=26)
    table.add_column("Model ID", style="dim", no_wrap=True)

    current_provider = None
    for i, m in enumerate(MODELS, 1):
        if m["provider"] != current_provider:
            if current_provider is not None:
                table.add_row("", "", "", "")  # spacer
            current_provider = m["provider"]
        table.add_row(str(i), m["provider"], m["name"], m["id"])

    table.add_row("", "", "", "")
    table.add_row("0", "", "[italic]Custom model ID[/italic]", "")
    console.print(table)
    console.print()

    choice = IntPrompt.ask("Enter number", default=1)

    if choice == 0:
        model_id = Prompt.ask("  Enter LiteLLM model ID")
        selected_model = None
    elif 1 <= choice <= len(MODELS):
        selected_model = MODELS[choice - 1]
        model_id = selected_model["id"]
    else:
        console.print("[red]Invalid choice[/red]")
        raise typer.Exit(1)

    console.print(f"  [green]>[/green] {model_id}\n")

    # ━━ Step 3: API key ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    console.print("[bold yellow]Step 3/3[/bold yellow]  [bold]API key[/bold]\n")

    needed_env = selected_model["key_env"] if selected_model else None
    provider_label = selected_model["provider"] if selected_model else None
    api_key, env_var, detected_label = _resolve_api_key(needed_env, provider_label)

    console.print(f"  [green]>[/green] Detected: [cyan]{detected_label}[/cyan] key\n")

    # ━━ Launch ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    cmd = [
        sys.executable, "-m", "yc_bench",
        "run",
        "--model", model_id,
        "--seed", str(seed),
        "--config", config_key,
    ]

    console.print(Panel.fit(
        f"[bold]yc-bench run[/bold] --model {model_id} --seed {seed} --config {config_key}",
        title="Launching",
        border_style="green",
    ))
    console.print()

    env = os.environ.copy()
    env[env_var] = api_key

    try:
        proc = subprocess.run(cmd, env=env)
        raise SystemExit(proc.returncode)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        raise typer.Exit(130)
