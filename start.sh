#!/usr/bin/env bash
set -e

# ── Install uv if missing ───────────────────────────────────────────────
if ! command -v uv &>/dev/null; then
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# ── Clone repo (skip if already inside it) ───────────────────────────────
if [ ! -f "pyproject.toml" ] || ! grep -q "yc.bench" pyproject.toml 2>/dev/null; then
  TMPDIR=$(mktemp -d)
  echo "Cloning yc-bench into $TMPDIR/yc-bench..."
  git clone --depth 1 https://github.com/collinear-ai/yc-bench.git "$TMPDIR/yc-bench"
  cd "$TMPDIR/yc-bench"
fi

# ── Install deps & launch ───────────────────────────────────────────────
# When piped via curl, stdin is the pipe — reattach to the terminal
# so interactive prompts work.
uv sync --quiet
exec uv run yc-bench start </dev/tty
