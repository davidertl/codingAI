import os
from pathlib import Path


def _repo_root() -> Path:
    """
    Resolve repo root (directory containing `ai-agent/`).
    Allow override via CODINGAI_HOME to support custom layouts.
    """
    override = os.getenv("CODINGAI_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    # ai-agent/paths.py -> ai-agent -> repo root
    return Path(__file__).resolve().parents[1]


REPO_ROOT = _repo_root()
AI_AGENT_DIR = (REPO_ROOT / "ai-agent").resolve()
SCRIPTS_DIR = (REPO_ROOT / "scripts").resolve()

_workspaces_raw = os.getenv("CODINGAI_WORKSPACES", "").strip()
WORKSPACES_DIR = Path(_workspaces_raw).expanduser().resolve() if _workspaces_raw else (REPO_ROOT / "workspaces").resolve()

_logs_raw = os.getenv("CODINGAI_LOGS_DIR", "").strip()
LOGS_DIR = Path(_logs_raw).expanduser().resolve() if _logs_raw else (AI_AGENT_DIR / "logs").resolve()

_state_raw = os.getenv("CODINGAI_STATE_FILE", "").strip()
STATE_FILE = Path(_state_raw).expanduser().resolve() if _state_raw else (AI_AGENT_DIR / "state.json").resolve()

_env_raw = os.getenv("CODINGAI_ENV_FILE", "").strip()
ENV_FILE = Path(_env_raw).expanduser().resolve() if _env_raw else (AI_AGENT_DIR / ".env").resolve()

_pem_raw = os.getenv("GITHUB_APP_PEM", "").strip()
GITHUB_APP_PEM_FILE = (
    Path(_pem_raw).expanduser().resolve()
    if _pem_raw
    else (AI_AGENT_DIR / "github_app" / "KRT-AI-Agent.pem").resolve()
)
