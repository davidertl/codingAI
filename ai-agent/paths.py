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
_PEM_DIR = (AI_AGENT_DIR / "github_app").resolve()
_PEM_CANONICAL = (_PEM_DIR / "github-app.pem").resolve()


def _resolve_pem_path() -> Path:
    if _pem_raw:
        return Path(_pem_raw).expanduser().resolve()
    if _PEM_CANONICAL.exists():
        return _PEM_CANONICAL
    if _PEM_DIR.exists():
        candidates = sorted(_PEM_DIR.glob("*.pem"))
        if candidates:
            return candidates[0].resolve()
    return _PEM_CANONICAL


GITHUB_APP_PEM_FILE = _resolve_pem_path()


def setup_status() -> dict:
    """Return whether core setup inputs exist (env IDs + pem file)."""
    # Import lazily to avoid circular dependency at startup.
    from core.secret_store import secrets as secret_store

    # Vault → os.environ fallback for each key
    env_vars = {
        "GITHUB_OWNER": secret_store.read("GITHUB_OWNER"),
        "GITHUB_APP_ID": secret_store.read("GITHUB_APP_ID"),
        "GITHUB_INSTALLATION_ID": secret_store.read("GITHUB_INSTALLATION_ID"),
    }
    pem_exists = GITHUB_APP_PEM_FILE.exists() or secret_store.has_pem()
    return {
        "has_owner": bool(env_vars["GITHUB_OWNER"]),
        "has_app_id": bool(env_vars["GITHUB_APP_ID"]),
        "has_installation_id": bool(env_vars["GITHUB_INSTALLATION_ID"]),
        "pem_exists": pem_exists,
        "setup_complete": all(env_vars.values()) and pem_exists,
    }
