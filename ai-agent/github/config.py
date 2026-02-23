import os

from paths import ENV_FILE


def _read_env_file_value(key: str) -> str:
    if not os.path.exists(str(ENV_FILE)):
        return ""
    try:
        with open(str(ENV_FILE), "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == key:
                    return v.strip()
    except Exception:
        return ""
    return ""


def get_github_owner() -> str:
    owner = _read_env_file_value("GITHUB_OWNER") or os.getenv("GITHUB_OWNER", "").strip()
    return owner
