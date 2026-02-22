#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT/ai-agent"
PYTHONPATH=. "$REPO_ROOT/ai-agent/venv/bin/python" -m uvicorn service.api:app --host "$HOST" --port "$PORT"
