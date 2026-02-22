#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

cd /home/codingai/ai-agent
PYTHONPATH=. /home/codingai/ai-agent/venv/bin/python -m uvicorn service.api:app --host "$HOST" --port "$PORT"
