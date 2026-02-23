#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEFAULT_VENV_DIR="$REPO_ROOT/ai-agent/venv"
FALLBACK_VENV_DIR="$REPO_ROOT/ai-agent/venv_user"
VENV_DIR="${VENV_DIR:-$DEFAULT_VENV_DIR}"

if [[ "$VENV_DIR" == "$DEFAULT_VENV_DIR" ]] && [[ -x "$FALLBACK_VENV_DIR/bin/python" ]]; then
  VENV_DIR="$FALLBACK_VENV_DIR"
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "Python interpreter not found in venv: $VENV_DIR" >&2
  echo "Create it with: python3 -m venv \"$VENV_DIR\" && \"$VENV_DIR/bin/pip\" install -r \"$REPO_ROOT/ai-agent/requirements.txt\"" >&2
  exit 1
fi

cd "$REPO_ROOT/ai-agent"
PYTHONPATH=. "$VENV_DIR/bin/python" -m uvicorn service.api:app --host "$HOST" --port "$PORT"
