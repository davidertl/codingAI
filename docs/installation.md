# Installation (Fresh Debian CLI)

This is a minimal, interactive install path for a brand new Debian server (no GUI).

The installer (`scripts/install_codingai.sh`) installs:

1. Python + pip + venv
2. Node.js 20 LTS (via apt or NodeSource fallback)
3. Common build tools (`build-essential`, headers)
4. Playwright OS dependencies (via `npx playwright install-deps`)
5. Docker engine + Compose plugin
6. Creates `ai-agent/venv` and installs Python deps
7. Writes `ai-agent/.env` interactively (GitHub App IDs, OpenAI key optional, local LLM URL/model)

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

## Notes

- Script uses `sudo` when needed for apt; run from repo root.
- Default data paths are relative to the cloned repo; see `ai-agent/paths.py` for overrides.
