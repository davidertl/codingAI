# Installation (Fresh Debian CLI)
Version: 1.2.0

This is a minimal, interactive install path for a brand new Debian server (no GUI).

The installer (`scripts/install_codingai.sh`) installs:

1. Python + pip + venv
2. Node.js 20 LTS (via apt or NodeSource fallback)
3. Common build tools (`build-essential`, headers)
4. Playwright OS dependencies (via `npx playwright install-deps`)
5. Docker engine + Compose plugin
6. Creates `ai-agent/venv` and installs Python deps
7. Writes `ai-agent/.env` interactively (GitHub App IDs, OpenAI key optional, local LLM URL/model)
8. Prepares secrets volume mount points (`ai-agent/.env`, `ai-agent/github_app/*.pem`) with restrictive permissions

## Option A (recommended): clone into a folder

```bash
sudo apt-get update -y
sudo apt-get install -y git

cd ~
git clone https://github.com/<user>/codingAI.git codingAI
cd ~/codingAI

bash scripts/install_codingai.sh
```

## Option B: place `scripts/` directly under `~`

If you intentionally want to run `bash ~/scripts/install_codingai.sh`, the `scripts/` directory must exist directly under your home directory.

One simple way after cloning:

```bash
cd ~/codingAI
mkdir -p ~/scripts
cp -a scripts/install_codingai.sh ~/scripts/

bash ~/scripts/install_codingai.sh
```

## Secrets & permissions (compose)

- `.env` is bind-mounted into the container; keep it at `ai-agent/.env` (or set `CODINGAI_ENV_FILE`) with `chmod 600`.
- GitHub App private key is stored under `ai-agent/github_app/` (default `KRT-AI-Agent.pem`) and bind-mounted; ensure the folder exists and `chmod 700 ai-agent/github_app && chmod 600 ai-agent/github_app/*.pem`.
- If you run Docker as non-root, keep ownership consistent so the container can read the files (same UID/GID as the host user running `docker compose`).

## Required manual step: GitHub App private key

The installer writes `ai-agent/.env` but cannot generate your GitHub App private key.

Copy your `.pem` file to:

- `ai-agent/github_app/KRT-AI-Agent.pem`

Then lock permissions:

```bash
chmod 600 ai-agent/github_app/KRT-AI-Agent.pem
```

## Start the Web UI

```bash
HOST=0.0.0.0 PORT=8000 scripts/run_control_api.sh
```

UI:

- `http://<vm-host>:8000/` (or `/ui`)

## Docker Compose (alternative)

```bash
CODINGAI_HTTP_PORT=8000 docker compose up --build
```

Mounts:
- `ai-agent/.env` (read-only)
- `ai-agent/github_app/` (read-only for `.pem`)
- `ai-agent/logs`, `ai-agent/state.json`, `workspaces/` (writable)

## Notes

- Script uses `sudo` when needed for apt; run from repo root.
- Default data paths are relative to the cloned repo; see `ai-agent/paths.py` for overrides.
