#!/usr/bin/env bash
set -euo pipefail

MODEL_REQUESTED="${1:-auto}"
CONTAINER_NAME="${OLLAMA_CONTAINER_NAME:-codingai-ollama}"
PORT="${OLLAMA_PORT:-11434}"
DATA_VOLUME="${OLLAMA_VOLUME:-codingai-ollama-data}"
CPU_FALLBACK_CANDIDATES="${LOCAL_LLM_CPU_CANDIDATES:-gpt-j:6b,llama2:7b,mistral:7b}"
GPU16_CANDIDATES="${LOCAL_LLM_GPU16_CANDIDATES:-mistral:7b,qwen2.5-coder:7b,starcoder2:7b}"
GPU24_CANDIDATES="${LOCAL_LLM_GPU24_CANDIDATES:-qwen2.5:14b,mistral:7b,qwen2.5-coder:7b}"

trim() {
  local value="${1:-}"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

split_csv_into_array() {
  local csv="${1:-}"
  local target_name="${2:-}"
  if [[ -z "$target_name" ]]; then
    echo "split_csv_into_array requires an output variable name." >&2
    return 1
  fi
  local -n target_ref="$target_name"
  target_ref=()
  IFS=',' read -ra raw <<<"$csv"
  for item in "${raw[@]}"; do
    item="$(trim "$item")"
    if [[ -n "$item" ]]; then
      target_ref+=("$item")
    fi
  done
}

detect_vram_gb() {
  if ! command -v nvidia-smi >/dev/null 2>&1; then
    return 1
  fi
  local vram_mb
  vram_mb="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -n1 | tr -dc '0-9')"
  if [[ -z "$vram_mb" ]]; then
    return 1
  fi
  printf '%s' "$((vram_mb / 1024))"
  return 0
}

pick_candidates() {
  local requested="${1:-auto}"
  local out_name="${2:-}"
  if [[ -z "$out_name" ]]; then
    echo "pick_candidates requires an output variable name." >&2
    return 1
  fi
  local -n out_ref="$out_name"
  local requested_profile=""
  if [[ "$requested" == "auto" ]]; then
    requested_profile="$(trim "${LOCAL_LLM_PROFILE:-}")"
    requested_profile="$(printf '%s' "$requested_profile" | tr '[:upper:]' '[:lower:]')"
  else
    requested_profile="$(printf '%s' "$requested" | tr '[:upper:]' '[:lower:]')"
  fi

  if [[ "$requested_profile" == "gpu16" ]]; then
    split_csv_into_array "$GPU16_CANDIDATES" "$out_name"
    echo "Model profile: gpu16 (forced)"
    return 0
  fi
  if [[ "$requested_profile" == "gpu24" ]]; then
    split_csv_into_array "$GPU24_CANDIDATES" "$out_name"
    echo "Model profile: gpu24 (forced)"
    return 0
  fi
  if [[ "$requested_profile" == "cpu" ]]; then
    split_csv_into_array "$CPU_FALLBACK_CANDIDATES" "$out_name"
    echo "Model profile: cpu (forced)"
    return 0
  fi

  if [[ "$requested" != "auto" ]]; then
    split_csv_into_array "$requested" "$out_name"
    return 0
  fi

  local vram_gb=""
  if vram_gb="$(detect_vram_gb)"; then
    if [[ "$vram_gb" -ge 24 ]]; then
      split_csv_into_array "$GPU24_CANDIDATES" "$out_name"
      echo "Model profile: gpu>=24gb (detected ${vram_gb}GB VRAM)"
      return 0
    fi
    split_csv_into_array "$GPU16_CANDIDATES" "$out_name"
    echo "Model profile: gpu<24gb (detected ${vram_gb}GB VRAM)"
    return 0
  fi

  split_csv_into_array "$CPU_FALLBACK_CANDIDATES" "$out_name"
  echo "Model profile: cpu-only fallback (no NVIDIA GPU detected)"
  return 0
}

pull_first_available_model() {
  local -n candidates_ref="$1"
  local selected=""
  local failed=()

  if [[ "${#candidates_ref[@]}" -eq 0 ]]; then
    echo "No model candidates resolved." >&2
    return 1
  fi

  echo "Resolved model candidates (priority order): ${candidates_ref[*]}" >&2
  for model in "${candidates_ref[@]}"; do
    echo "Pulling model: ${model}" >&2
    if docker exec "$CONTAINER_NAME" ollama pull "$model"; then
      selected="$model"
      break
    fi
    failed+=("$model")
    echo "Model pull failed: ${model}" >&2
  done

  if [[ -z "$selected" ]]; then
    echo "Could not pull any local model. Tried: ${failed[*]}" >&2
    return 1
  fi

  printf '%s' "$selected"
  return 0
}

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

if ! docker exec "$CONTAINER_NAME" ollama list >/dev/null 2>&1; then
  echo "Ollama service did not become ready in time." >&2
  exit 1
fi

MODEL_CANDIDATES=()
pick_candidates "$MODEL_REQUESTED" MODEL_CANDIDATES
SELECTED_MODEL="$(pull_first_available_model MODEL_CANDIDATES)"

echo
echo "Local LLM is ready. Export these env vars before running CodingAI:"
cat <<EOF
export LLM_PROVIDER=local
export LOCAL_LLM_BASE_URL=http://127.0.0.1:${PORT}
export LOCAL_LLM_API_MODE=chat
export LOCAL_LLM_MODEL=${SELECTED_MODEL}
EOF
echo "SELECTED_MODEL=${SELECTED_MODEL}"

if command -v curl >/dev/null 2>&1; then
  echo
  echo "Local model endpoint check:"
  curl -s "http://127.0.0.1:${PORT}/v1/models" | head -c 600 || true
  echo
fi
