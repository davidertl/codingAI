#!/usr/bin/env bash
set -euo pipefail

# Minimal interactive installer for a fresh Debian CLI host.
# - installs required OS packages
# - creates venv and installs Python deps
# - writes ai-agent/.env interactively (no secrets committed to git)
# - reminds user to copy GitHub App .pem into place

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

AI_AGENT_DIR="$REPO_ROOT/ai-agent"
PRIMARY_VENV_DIR="$AI_AGENT_DIR/venv"
FALLBACK_VENV_DIR="$AI_AGENT_DIR/venv_user"
VENV_DIR="${VENV_DIR:-$PRIMARY_VENV_DIR}"
ENV_FILE="$AI_AGENT_DIR/.env"
PEM_TARGET="$AI_AGENT_DIR/github_app/KRT-AI-Agent.pem"

if [[ ! -d "$AI_AGENT_DIR" ]]; then
  echo "Expected ai-agent directory at: $AI_AGENT_DIR" >&2
  exit 1
fi

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    echo "Re-running with sudo (required for apt installs)..." >&2
    exec sudo -H bash "$0" "$@"
  fi
  echo "This installer needs root for apt installs, but sudo is not available." >&2
  echo "Re-run as root: sudo bash scripts/install_codingai.sh" >&2
  exit 1
fi

if [[ -f /etc/os-release ]]; then
  . /etc/os-release || true
  if [[ "${ID:-}" != "debian" ]]; then
    echo "Warning: this installer was written for Debian; detected ID='${ID:-unknown}'." >&2
  fi
fi

install_node20() {
  if command -v node >/dev/null 2>&1; then
    local ver major
    ver="$(node -v 2>/dev/null || true)"
    major="${ver#v}"
    major="${major%%.*}"
    if [[ "$major" == "20" ]]; then
      return 0
    fi
  fi

  apt-get install -y --no-install-recommends nodejs npm || true

  if command -v node >/dev/null 2>&1; then
    local ver major
    ver="$(node -v 2>/dev/null || true)"
    major="${ver#v}"
    major="${major%%.*}"
    if [[ "$major" == "20" ]]; then
      return 0
    fi
  fi

  echo "Installing Node.js 20 LTS via NodeSource..." >&2
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y --no-install-recommends nodejs
}

apt-get update -y
apt-get install -y --no-install-recommends \
  ca-certificates \
  curl \
  git \
  gnupg \
  build-essential \
  pkg-config \
  python3 \
  python3-dev \
  python3-pip \
  python3-venv \
  unzip \
  wget \
  docker-cli \
  docker.io \
  docker-compose

if ! command -v docker >/dev/null 2>&1; then
  echo "docker CLI still missing after apt install; retrying docker-cli..." >&2
  apt-get install -y --no-install-recommends docker-cli
fi

mkdir -p "$AI_AGENT_DIR/logs" "$REPO_ROOT/workspaces" "$AI_AGENT_DIR/github_app"

install_node20

if command -v systemctl >/dev/null 2>&1; then
  systemctl enable --now docker >/dev/null 2>&1 || true
fi

INSTALL_USER="${INSTALL_USER:-${SUDO_USER:-}}"
INSTALL_GROUP=""
if [[ -n "$INSTALL_USER" ]] && id "$INSTALL_USER" >/dev/null 2>&1; then
  INSTALL_GROUP="$(id -gn "$INSTALL_USER")"
  groupadd -f docker >/dev/null 2>&1 || true
  usermod -aG docker "$INSTALL_USER" >/dev/null 2>&1 || true
fi

run_as_install_user() {
  if [[ -n "$INSTALL_USER" ]] && id "$INSTALL_USER" >/dev/null 2>&1; then
    sudo -H -u "$INSTALL_USER" "$@"
  else
    "$@"
  fi
}

if [[ "$VENV_DIR" == "$PRIMARY_VENV_DIR" ]] && [[ -d "$PRIMARY_VENV_DIR" ]]; then
  if [[ -n "$INSTALL_USER" ]] && id "$INSTALL_USER" >/dev/null 2>&1; then
    if ! sudo -H -u "$INSTALL_USER" test -w "$PRIMARY_VENV_DIR"; then
      echo "Detected venv not writable by $INSTALL_USER at $PRIMARY_VENV_DIR; fixing ownership." >&2
      chown -R "$INSTALL_USER:$INSTALL_GROUP" "$PRIMARY_VENV_DIR" || true
    fi
    if ! sudo -H -u "$INSTALL_USER" test -w "$PRIMARY_VENV_DIR"; then
      echo "Venv still not writable by $INSTALL_USER; switching to $FALLBACK_VENV_DIR" >&2
      VENV_DIR="$FALLBACK_VENV_DIR"
    fi
  elif [[ ! -w "$PRIMARY_VENV_DIR" ]]; then
    echo "Detected non-writable venv at $PRIMARY_VENV_DIR; switching to $FALLBACK_VENV_DIR" >&2
    VENV_DIR="$FALLBACK_VENV_DIR"
  fi
fi

if [[ ! -d "$VENV_DIR" ]]; then
  run_as_install_user python3 -m venv "$VENV_DIR"
fi

run_as_install_user "$VENV_DIR/bin/pip" install --upgrade pip >/dev/null
run_as_install_user "$VENV_DIR/bin/pip" install -r "$AI_AGENT_DIR/requirements.txt"

INSTALL_PLAYWRIGHT_DEPS="${INSTALL_PLAYWRIGHT_DEPS:-1}"
if [[ "$INSTALL_PLAYWRIGHT_DEPS" == "1" ]]; then
  echo "Installing Playwright OS dependencies..." >&2
  # This only installs OS deps; it does not download browsers.
  npx --yes playwright@latest install-deps
fi

prompt() {
  local var="$1"
  local text="$2"
  local def="${3:-}"
  local val=""
  if [[ -n "$def" ]]; then
    read -r -p "$text [$def]: " val
    val="${val:-$def}"
  else
    read -r -p "$text: " val
  fi
  printf '%s=%s\n' "$var" "$val"
}

prompt_secret() {
  local var="$1"
  local text="$2"
  local val=""
  read -r -s -p "$text: " val
  echo
  printf '%s=%s\n' "$var" "$val"
}

read_env_entry() {
  local env_file="$1"
  local env_key="$2"
  awk -F= -v target="$env_key" '
    /^[[:space:]]*#/ { next }
    /^[[:space:]]*$/ { next }
    {
      key = $1
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
      if (key == target) {
        sub(/^[^=]*=/, "", $0)
        print $0
        exit
      }
    }
  ' "$env_file"
}

upsert_env_entry() {
  local env_file="$1"
  local env_key="$2"
  local env_value="$3"
  local tmp_file=""

  if [[ "$env_key" == *$'\n'* ]] || [[ "$env_key" == *$'\r'* ]]; then
    echo "Invalid env key (contains newline): $env_key" >&2
    return 1
  fi
  if [[ "$env_value" == *$'\n'* ]] || [[ "$env_value" == *$'\r'* ]]; then
    echo "Invalid env value for $env_key (contains newline)." >&2
    return 1
  fi

  tmp_file="$(mktemp "${env_file}.tmp.XXXXXX")"
  awk -v key="$env_key" -v value="$env_value" '
    BEGIN { updated = 0 }
    {
      line = $0
      if (line ~ /^[[:space:]]*#/ || line ~ /^[[:space:]]*$/) {
        print line
        next
      }
      split(line, kv, "=")
      k = kv[1]
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", k)
      if (k == key) {
        print key "=" value
        updated = 1
        next
      }
      print line
    }
    END {
      if (!updated) {
        print key "=" value
      }
    }
  ' "$env_file" > "$tmp_file"
  mv "$tmp_file" "$env_file"
}

if [[ -f "$ENV_FILE" ]]; then
  echo "Found existing $ENV_FILE. It will be backed up and replaced."
  cp -a "$ENV_FILE" "$ENV_FILE.bak.$(date +%Y%m%d%H%M%S)"
fi

echo "Writing $ENV_FILE (interactive)..."
{
  echo "# Generated by scripts/install_codingai.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo
  echo "# GitHub App values are optional here; you can set/update them later in the Web UI."
  prompt "GITHUB_OWNER" "GitHub owner/org (optional)"
  prompt "GITHUB_APP_ID" "GitHub App ID (optional)"
  prompt "GITHUB_INSTALLATION_ID" "GitHub App Installation ID (optional)"
  echo
  prompt "LLM_PROVIDER" "LLM provider (openai|local|auto)" "openai"
  prompt "LLM_PROVIDER_ORDER" "Provider order when LLM_PROVIDER=auto" "local,openai"
  prompt "LLM_FALLBACK_ENABLED" "Enable provider fallback (true|false)" "true"
  prompt "LLM_REQUIRE_HEALTHY" "Require healthy provider (true|false)" "true"
  echo
  prompt "OPENAI_BASE_URL" "OpenAI base URL" "https://api.openai.com"
  prompt_secret "OPENAI_API_KEY" "OpenAI API key (leave empty to run local-only)"
  echo
  prompt "LOCAL_LLM_BASE_URL" "Local LLM base URL" "http://127.0.0.1:11434"
  prompt "LOCAL_LLM_API_MODE" "Local LLM API mode (chat|responses)" "chat"
  prompt "LOCAL_LLM_PROFILE" "Local LLM Profile (auto|gpu16|gpu24|cpu)" "auto"
  prompt "LOCAL_LLM_MODEL" "Local LLM model name (optional)" ""
} >"$ENV_FILE"

chmod 600 "$ENV_FILE" || true
if [[ -n "$INSTALL_USER" ]] && [[ -n "$INSTALL_GROUP" ]]; then
  chown "$INSTALL_USER:$INSTALL_GROUP" "$ENV_FILE" || true
  chown -R "$INSTALL_USER:$INSTALL_GROUP" "$VENV_DIR" "$AI_AGENT_DIR/logs" "$AI_AGENT_DIR/github_app" "$REPO_ROOT/workspaces" || true
fi

echo
LOCAL_LLM_MODEL_INSTALL_DEFAULT="$(read_env_entry "$ENV_FILE" "LOCAL_LLM_PROFILE")"
LOCAL_LLM_MODEL_INSTALL_DEFAULT="${LOCAL_LLM_MODEL_INSTALL_DEFAULT:-auto}"
LOCAL_LLM_PULL_MODE="${LOCAL_LLM_PULL_MODE:-background}"
echo "Local LLM model profile options:"
echo "  - auto (recommended): GPU/VRAM based profile"
echo "  - fixed profile: gpu16 | gpu24 | cpu"
echo "  - explicit model: mistral:7b | qwen2.5-coder:7b | starcoder2:7b | qwen2.5:14b"
echo "  - explicit CSV fallback chain: mistral:7b,qwen2.5-coder:7b,starcoder2:7b"
read -r -p "Local LLM model/profile to install [$LOCAL_LLM_MODEL_INSTALL_DEFAULT]: " LOCAL_LLM_MODEL_INSTALL
LOCAL_LLM_MODEL_INSTALL="${LOCAL_LLM_MODEL_INSTALL:-$LOCAL_LLM_MODEL_INSTALL_DEFAULT}"
if [[ "$LOCAL_LLM_PULL_MODE" == "background" ]]; then
  echo "Installing local LLM runtime via Ollama Docker (background model pull)..."
else
  echo "Installing local LLM runtime via Ollama Docker (foreground model pull)..."
fi
LOCAL_LLM_INSTALL_LOG="$(mktemp)"
if ! run_as_install_user env \
  LOCAL_LLM_PULL_MODE="$LOCAL_LLM_PULL_MODE" \
  LOCAL_LLM_ENV_FILE="$ENV_FILE" \
  bash "$REPO_ROOT/scripts/setup_local_llm_ollama.sh" "$LOCAL_LLM_MODEL_INSTALL" 2>&1 | tee "$LOCAL_LLM_INSTALL_LOG"; then
  rm -f "$LOCAL_LLM_INSTALL_LOG"
  echo "Local LLM install step failed. See output above." >&2
  exit 1
fi

SELECTED_MODEL="$(sed -n 's/^SELECTED_MODEL=//p' "$LOCAL_LLM_INSTALL_LOG" | tail -n1 | tr -d '\r')"
BACKGROUND_PULL_PID="$(sed -n 's/^BACKGROUND_PULL_PID=//p' "$LOCAL_LLM_INSTALL_LOG" | tail -n1 | tr -d '\r')"
BACKGROUND_PULL_LOG="$(sed -n 's/^BACKGROUND_PULL_LOG=//p' "$LOCAL_LLM_INSTALL_LOG" | tail -n1 | tr -d '\r')"
BACKGROUND_PULL_RESULT="$(sed -n 's/^BACKGROUND_PULL_RESULT=//p' "$LOCAL_LLM_INSTALL_LOG" | tail -n1 | tr -d '\r')"
rm -f "$LOCAL_LLM_INSTALL_LOG"
if [[ -z "$SELECTED_MODEL" ]]; then
  echo "Local LLM install did not report SELECTED_MODEL. Aborting to avoid inconsistent .env." >&2
  exit 1
fi
upsert_env_entry "$ENV_FILE" "LOCAL_LLM_MODEL" "$SELECTED_MODEL"
case "$LOCAL_LLM_MODEL_INSTALL" in
  auto|gpu16|gpu24|cpu)
    upsert_env_entry "$ENV_FILE" "LOCAL_LLM_PROFILE" "$LOCAL_LLM_MODEL_INSTALL"
    ;;
esac
if [[ -n "$INSTALL_USER" ]] && [[ -n "$INSTALL_GROUP" ]]; then
  chown "$INSTALL_USER:$INSTALL_GROUP" "$ENV_FILE" || true
fi
if [[ "$LOCAL_LLM_PULL_MODE" == "background" ]]; then
  echo "Local model download continues in background (pid: ${BACKGROUND_PULL_PID:-unknown})."
  if [[ -n "$BACKGROUND_PULL_LOG" ]]; then
    echo "Follow progress: tail -f \"$BACKGROUND_PULL_LOG\""
  fi
  if [[ -n "$BACKGROUND_PULL_RESULT" ]]; then
    echo "Result file: $BACKGROUND_PULL_RESULT"
  fi
fi

echo
PUSH_REMOTE_URL=""
while [[ -z "$PUSH_REMOTE_URL" ]]; do
  read -r -p "Remote URL to push API snapshot (required): " PUSH_REMOTE_URL
done
read -r -p "Remote branch [localstate]: " PUSH_BRANCH
PUSH_BRANCH="${PUSH_BRANCH:-localstate}"
read -r -p "AUTHOR_MODE (bot|custom) [bot]: " PUSH_AUTHOR_MODE
PUSH_AUTHOR_MODE="${PUSH_AUTHOR_MODE:-bot}"
read -r -p "Commit message [chore: update control-plane after local-llm install]: " PUSH_COMMIT_MSG
PUSH_COMMIT_MSG="${PUSH_COMMIT_MSG:-chore: update control-plane after local-llm install}"

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  read -r -s -p "GitHub token for push (repo scope): " PUSH_GITHUB_TOKEN
  echo
else
  PUSH_GITHUB_TOKEN="${GITHUB_TOKEN}"
fi

if [[ -z "${PUSH_GITHUB_TOKEN:-}" ]]; then
  echo "GitHub token is required for push step." >&2
  exit 1
fi

PUSH_AUTHOR_NAME=""
PUSH_AUTHOR_EMAIL=""
if [[ "$PUSH_AUTHOR_MODE" == "custom" ]]; then
  read -r -p "Custom author name: " PUSH_AUTHOR_NAME
  read -r -p "Custom author email: " PUSH_AUTHOR_EMAIL
fi

echo "Pushing API snapshot to $PUSH_REMOTE_URL ($PUSH_BRANCH)..."
run_as_install_user env \
  GITHUB_TOKEN="$PUSH_GITHUB_TOKEN" \
  AUTHOR_MODE="$PUSH_AUTHOR_MODE" \
  AUTHOR_NAME="$PUSH_AUTHOR_NAME" \
  AUTHOR_EMAIL="$PUSH_AUTHOR_EMAIL" \
  COMMIT_MSG="$PUSH_COMMIT_MSG" \
  bash "$REPO_ROOT/scripts/push_localstate.sh" "$REPO_ROOT" "$PUSH_REMOTE_URL" "$PUSH_BRANCH"

echo
echo "IMPORTANT: copy your GitHub App private key (.pem) to:"
echo "  $PEM_TARGET"
echo "You can scp it, then verify permissions (recommended 600)."
echo
if [[ -n "$INSTALL_USER" ]]; then
  echo "Docker group: user '$INSTALL_USER' was added to 'docker'. Re-login for it to take effect."
  echo
fi
echo "Run the control plane + Web UI:"
echo "  cd \"$REPO_ROOT\""
echo "  HOST=0.0.0.0 PORT=8000 scripts/run_control_api.sh"
echo
echo "Health check:"
echo "  curl -s http://127.0.0.1:8000/health"
