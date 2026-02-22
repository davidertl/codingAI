#!/usr/bin/env bash
set -euo pipefail

MODEL="${1:-qwen2.5-coder:7b}"
CONTAINER_NAME="${OLLAMA_CONTAINER_NAME:-codingai-ollama}"
PORT="${OLLAMA_PORT:-11434}"
DATA_VOLUME="${OLLAMA_VOLUME:-codingai-ollama-data}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found. Install Docker first." >&2
  exit 1
fi

if ! docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
  docker run -d \
    --name "$CONTAINER_NAME" \
    -p "${PORT}:11434" \
    -v "${DATA_VOLUME}:/root/.ollama" \
    ollama/ollama >/dev/null
else
  docker start "$CONTAINER_NAME" >/dev/null || true
fi

echo "Waiting for Ollama service..."
for _ in $(seq 1 30); do
  if docker exec "$CONTAINER_NAME" ollama list >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "Pulling model: ${MODEL}"
docker exec "$CONTAINER_NAME" ollama pull "$MODEL"

echo
echo "Local LLM is ready. Export these env vars before running CodingAI:"
cat <<EOF
export LLM_PROVIDER=local
export LOCAL_LLM_BASE_URL=http://127.0.0.1:${PORT}
export LOCAL_LLM_API_MODE=chat
export LOCAL_LLM_MODEL=${MODEL}
EOF

if command -v curl >/dev/null 2>&1; then
  echo
  echo "Local model endpoint check:"
  curl -s "http://127.0.0.1:${PORT}/v1/models" | head -c 600 || true
  echo
fi
