#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

MODEL_REQUESTED="auto"
PULL_MODE="${LOCAL_LLM_PULL_MODE:-background}" # background | foreground

POSITIONAL_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --background)
      PULL_MODE="background"
      ;;
    --foreground)
      PULL_MODE="foreground"
      ;;
    -h|--help)
      cat <<'EOF'
Usage: setup_local_llm_ollama.sh [--background|--foreground] [model_or_profile]
  model_or_profile:
    - auto (default)
    - gpu16 | gpu24 | cpu
    - explicit model (e.g. mistral:7b)
    - CSV chain (e.g. mistral:7b,qwen2.5-coder:7b)
EOF
      exit 0
      ;;
    *)
      POSITIONAL_ARGS+=("$arg")
      ;;
  esac
done

if [[ "${#POSITIONAL_ARGS[@]}" -gt 1 ]]; then
  echo "Only one model/profile argument is allowed." >&2
  exit 1
fi
if [[ "${#POSITIONAL_ARGS[@]}" -eq 1 ]]; then
  MODEL_REQUESTED="${POSITIONAL_ARGS[0]}"
fi

CONTAINER_NAME="${OLLAMA_CONTAINER_NAME:-codingai-ollama}"
PORT="${OLLAMA_PORT:-11434}"
DATA_VOLUME="${OLLAMA_VOLUME:-codingai-ollama-data}"
CPU_FALLBACK_CANDIDATES="${LOCAL_LLM_CPU_CANDIDATES:-tinyllama:1.1b,mistral:7b,llama2:7b}"
GPU16_CANDIDATES="${LOCAL_LLM_GPU16_CANDIDATES:-mistral:7b,qwen2.5-coder:7b,starcoder2:7b}"
GPU24_CANDIDATES="${LOCAL_LLM_GPU24_CANDIDATES:-qwen2.5:14b,mistral:7b,qwen2.5-coder:7b}"
PULL_LOG_DIR="${LOCAL_LLM_PULL_LOG_DIR:-$REPO_ROOT/ai-agent/logs}"
PULL_LOG_FILE="${LOCAL_LLM_PULL_LOG_FILE:-$PULL_LOG_DIR/local_llm_pull.log}"
PULL_RESULT_FILE="${LOCAL_LLM_PULL_RESULT_FILE:-$PULL_LOG_DIR/local_llm_pull.result}"
ENV_SYNC_FILE="${LOCAL_LLM_ENV_FILE:-}"

trim() {
  local value="${1:-}"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

timestamp_utc() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

append_pull_log() {
  local line="${1:-}"
  mkdir -p "$PULL_LOG_DIR"
  printf '%s %s\n' "$(timestamp_utc)" "$line" >> "$PULL_LOG_FILE"
}

upsert_local_llm_model_env() {
  local env_file="${1:-}"
  local selected_model="${2:-}"
  if [[ -z "$env_file" ]] || [[ -z "$selected_model" ]]; then
    return 0
  fi
  if [[ ! -f "$env_file" ]] || [[ ! -w "$env_file" ]]; then
    return 0
  fi
  local tmp_file
  tmp_file="$(mktemp "${env_file}.tmp.XXXXXX")"
  awk -v model="$selected_model" '
    BEGIN { updated = 0 }
    {
      line = $0
      if (line ~ /^[[:space:]]*#/ || line ~ /^[[:space:]]*$/) {
        print line
        next
      }
      split(line, kv, "=")
      key = kv[1]
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
      if (key == "LOCAL_LLM_MODEL") {
        print "LOCAL_LLM_MODEL=" model
        updated = 1
        next
      }
      print line
    }
    END {
      if (!updated) {
        print "LOCAL_LLM_MODEL=" model
      }
    }
  ' "$env_file" > "$tmp_file"
  mv "$tmp_file" "$env_file"
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

start_background_pull() {
  local -n candidates_ref="$1"
  local env_file="${2:-}"
  local bg_pid=""

  mkdir -p "$PULL_LOG_DIR"
  : > "$PULL_LOG_FILE"
  printf 'status=running\nselected_model=\n' > "$PULL_RESULT_FILE"
  append_pull_log "Background local model pull started. candidates=${candidates_ref[*]}"

  (
    set +e
    local selected=""
    local failed=()
    for model in "${candidates_ref[@]}"; do
      append_pull_log "Pulling model: ${model}"
      if docker exec "$CONTAINER_NAME" ollama pull "$model" >> "$PULL_LOG_FILE" 2>&1; then
        selected="$model"
        break
      fi
      failed+=("$model")
      append_pull_log "Model pull failed: ${model}"
    done

    if [[ -n "$selected" ]]; then
      printf 'status=ready\nselected_model=%s\n' "$selected" > "$PULL_RESULT_FILE"
      append_pull_log "Background local model pull finished. selected_model=${selected}"
      upsert_local_llm_model_env "$env_file" "$selected" || true
    else
      printf 'status=failed\nselected_model=\nfailed_candidates=%s\n' "${failed[*]}" > "$PULL_RESULT_FILE"
      append_pull_log "Background local model pull failed. failed_candidates=${failed[*]}"
    fi
  ) &
  bg_pid="$!"
  disown "$bg_pid" 2>/dev/null || true
  printf '%s' "$bg_pid"
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

if [[ "${#MODEL_CANDIDATES[@]}" -eq 0 ]]; then
  echo "No model candidates resolved." >&2
  exit 1
fi

SELECTED_MODEL=""
BACKGROUND_PULL_PID=""
if [[ "$PULL_MODE" == "background" ]]; then
  SELECTED_MODEL="${MODEL_CANDIDATES[0]}"
  BACKGROUND_PULL_PID="$(start_background_pull MODEL_CANDIDATES "$ENV_SYNC_FILE")"
else
  SELECTED_MODEL="$(pull_first_available_model MODEL_CANDIDATES)"
  upsert_local_llm_model_env "$ENV_SYNC_FILE" "$SELECTED_MODEL" || true
fi

echo
if [[ "$PULL_MODE" == "background" ]]; then
  echo "Local LLM pull is running in background (pid=${BACKGROUND_PULL_PID})."
  echo "Follow progress via:"
  echo "  tail -f ${PULL_LOG_FILE}"
  echo "Result status file:"
  echo "  ${PULL_RESULT_FILE}"
else
  echo "Local LLM is ready."
fi
echo "Export these env vars before running CodingAI:"
cat <<EOF
export LLM_PROVIDER=local
export LOCAL_LLM_BASE_URL=http://127.0.0.1:${PORT}
export LOCAL_LLM_API_MODE=chat
export LOCAL_LLM_MODEL=${SELECTED_MODEL}
EOF
echo "SELECTED_MODEL=${SELECTED_MODEL}"
if [[ "$PULL_MODE" == "background" ]]; then
  echo "BACKGROUND_PULL_PID=${BACKGROUND_PULL_PID}"
  echo "BACKGROUND_PULL_LOG=${PULL_LOG_FILE}"
  echo "BACKGROUND_PULL_RESULT=${PULL_RESULT_FILE}"
fi

if command -v curl >/dev/null 2>&1; then
  echo
  echo "Local model endpoint check:"
  curl -s "http://127.0.0.1:${PORT}/v1/models" | head -c 600 || true
  echo
fi
