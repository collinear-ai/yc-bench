# Agent Layer

**Location**: `src/yc_bench/agent/`

## Overview

The agent layer connects an LLM to the simulation via a tool-use interface. It manages the conversation loop, prompt construction, tool execution, and run state tracking.

## Architecture

```
┌─────────────────────────┐
│     Agent Loop          │
│  (loop.py)              │
├─────────────────────────┤
│  ┌──────────┐ ┌──────┐ │
│  │  Prompt   │ │ Tools │ │
│  │ Builder   │ │      │ │
│  └──────────┘ └──────┘ │
├─────────────────────────┤
│     LLM Runtime         │
│  (runtime/)             │
│  LiteLLM abstraction    │
├─────────────────────────┤
│  Run State / Transcript │
│  (run_state.py)         │
└─────────────────────────┘
```

## Design Choices

### LiteLLM as LLM Abstraction (`runtime/`)

The agent uses [LiteLLM](https://github.com/BerriAI/litellm) to abstract away vendor differences:

```python
# Supports: Anthropic, OpenAI, OpenRouter, Google Gemini, etc.
response = litellm.completion(
    model="anthropic/claude-sonnet-4-20250514",
    messages=messages,
    tools=tools,
)
```

**Why LiteLLM?**
- Single interface for all major LLM providers
- Consistent tool-use format across providers
- Easy to benchmark different models on the same scenarios
- Handles auth, retries, and format conversion

### Tool-Use Interface (Not Text Parsing)

The agent interacts via structured tool calls, not text command parsing:

```json
{
  "name": "run_command",
  "arguments": {
    "command": "yc-bench task list --status active"
  }
}
```

**Why tool-use?**
- Eliminates parsing ambiguity
- Works with all modern LLMs' native tool-use
- Structured output from CLI commands (JSON) flows cleanly back
- Reduces error rate vs. free-text command generation

### Available Tools

#### `run_command`
Executes CLI commands in a subprocess. The agent can run any `yc-bench` CLI command.

```python
def run_command(command: str) -> str:
    """Execute a yc-bench CLI command and return output."""
```

**Design choice**: Subprocess execution provides isolation. The agent can't accidentally modify simulation state outside of defined CLI commands.

#### `python_repl` (Optional)
A persistent Python interpreter for calculations and data analysis.

```python
def python_repl(code: str) -> str:
    """Execute Python code and return output."""
```

**Design choice**: Some agents benefit from being able to compute (e.g., calculate optimal assignments, project cash flow). This tool is optional and configurable.

## Agent Loop (`loop.py`)

### Main Loop

```python
def run_agent_loop(runtime, session, company_id, cfg):
    while not terminal:
        # Build messages (system prompt + history)
        messages = build_messages(history, context)

        # Call LLM
        response = runtime.completion(messages, tools)

        # Process tool calls
        for tool_call in response.tool_calls:
            result = execute_tool(tool_call)
            history.append(tool_call, result)

        # Check for terminal conditions
        if is_terminal(result):
            break

        # Auto-resume if agent hasn't advanced simulation
        if turns_since_resume > max_turns_without_resume:
            force_resume()
```

### Design Choices in the Loop

#### History Truncation

```python
# Keep only last N turns to fit context window
messages = system_prompt + history[-max_history_turns:]
```

**Why truncate?** Long simulations generate hundreds of turns. Without truncation, the context would exceed any model's window. The scratchpad CLI command compensates for lost history.

#### Auto-Resume Forcing

If the agent doesn't call `yc-bench sim resume` for N turns, the loop forces one:

```python
if turns_since_resume > cfg.loop.max_turns_without_resume:
    result = execute("yc-bench sim resume")
```

**Why force?** Some models get stuck in analysis loops, repeatedly querying state without advancing. Auto-resume prevents infinite loops and ensures forward progress.

#### Turn Budget

The loop has a maximum turn count. This prevents runaway agents and bounds benchmark cost.

## Prompt Construction (`prompt.py`)

### System Prompt Structure

```
1. Role description ("You are the CEO of an AI startup...")
2. Available commands reference
3. Current company status summary
4. Strategic guidance (domain, prestige, deadlines)
5. Constraints and rules
```

**Design choice**: The system prompt provides enough context for the agent to understand its role without revealing internal mechanics (like hidden skill rates or exact formulas).

### Context Building

Each turn, the prompt may include:
- Wake events from the last `sim resume`
- Current funds and runway
- Active task count and approaching deadlines
- Prestige levels

This contextual information helps the agent make informed decisions without needing to query every turn.

## Run State (`run_state.py`)

### Transcript Recording

Every turn is recorded:

```python
{
    "turn": 42,
    "messages": [...],
    "tool_calls": [...],
    "tool_results": [...],
    "timestamp": "2025-03-15T10:30:00",
    "tokens_used": 1500
}
```

**Design choice**: Full transcripts enable:
- Post-hoc analysis of agent strategy
- Debugging agent failures
- Benchmark scoring based on decision quality
- Comparison across models

### Output Format

The final rollout is saved as JSON:

```json
{
    "model": "anthropic/claude-sonnet-4-20250514",
    "seed": 42,
    "config": "medium",
    "outcome": "horizon_end",
    "final_funds": 250000,
    "final_prestige": {"research": 7.2, ...},
    "turns": 187,
    "transcript": [...]
}
```

## Command Execution Policy (`commands/`)

### Command Allowlist

The agent can only execute `yc-bench` CLI commands. Arbitrary shell commands are blocked.

**Design choice**: Restricting to the CLI API ensures:
- No direct database manipulation
- No simulation state bypass
- Fair comparison across models
- Deterministic state transitions

### Error Handling

Invalid commands return structured error messages:

```json
{"error": "Task not found", "task_id": "..."}
```

**Design choice**: Structured errors help the agent understand and recover from mistakes, rather than receiving opaque stack traces.

## Retry and Timeout Logic

```python
# Exponential backoff for LLM API calls
for attempt in range(max_retries):
    try:
        response = runtime.completion(messages, tools)
        break
    except RateLimitError:
        wait(2 ** attempt)
```

**Design choice**: LLM APIs are unreliable. Retry logic ensures transient failures don't corrupt benchmark runs.
