import os

from core.secret_store import secrets as secret_store


def get_github_owner() -> str:
    """Read GITHUB_OWNER from Vault → os.environ fallback."""
    return secret_store.read("GITHUB_OWNER")
