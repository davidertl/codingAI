"""
Unified secret store with Vault backend.

When VAULT_ENABLED=true:
  - Secrets are read/written from HashiCorp Vault (KV v2).
  - All writes also update os.environ so the running process sees changes
    immediately (hot-reload).
  - On startup, secrets are synced from Vault → os.environ.

When VAULT_ENABLED=false (default):
  - read() returns os.getenv() values only.
  - read_all() returns an empty dict (callers fall back to os.environ).
  - write() only updates os.environ (in-memory, not persisted — WebUI
    settings survive only until the container restarts; .env injected via
    Docker Compose env_file at boot provides the baseline).
  - This mode keeps the system fully functional without Vault.
"""

import base64
import logging
import os
from typing import Optional

_log = logging.getLogger("secret_store")

_TRUTHY = {"1", "true", "yes", "on"}

# ---------------------------------------------------------------------------
# Vault availability — hvac is optional; import lazily to avoid hard crash
# when the package is not installed or Vault is not enabled.
# ---------------------------------------------------------------------------

_hvac = None


def _ensure_hvac():
    global _hvac
    if _hvac is None:
        try:
            import hvac as _hvac_module
            _hvac = _hvac_module
        except ImportError:
            raise RuntimeError(
                "hvac package is required when VAULT_ENABLED=true.  "
                "Install it with:  pip install hvac>=2.1.0"
            )
    return _hvac


# ---------------------------------------------------------------------------
# SecretStore
# ---------------------------------------------------------------------------


class SecretStore:
    """Thin abstraction over HashiCorp Vault KV v2 (or env-only fallback)."""

    _KV_MOUNT = "secret"
    _KV_SECRETS_PATH = "codingai/secrets"
    _KV_PEM_PATH = "codingai/pem"

    def __init__(self) -> None:
        self._enabled: bool = False
        self._client = None  # hvac.Client or None
        self._refresh_config()

    # -- configuration ------------------------------------------------------

    def _refresh_config(self) -> None:
        """Re-read env vars that control Vault connectivity."""
        self._enabled = os.getenv("VAULT_ENABLED", "false").strip().lower() in _TRUTHY
        if not self._enabled:
            self._client = None
            return
        hvac = _ensure_hvac()
        addr = os.getenv("VAULT_ADDR", "http://vault:8200").strip()
        token = os.getenv("VAULT_TOKEN", "").strip()
        if not token:
            _log.warning("VAULT_ENABLED=true but VAULT_TOKEN is empty — Vault calls will fail")
        self._client = hvac.Client(url=addr, token=token)

    @property
    def enabled(self) -> bool:
        return self._enabled

    # -- health -------------------------------------------------------------

    def health(self) -> dict:
        """Return Vault health status for display in the UI."""
        if not self._enabled:
            return {"enabled": False, "connected": False, "sealed": None, "version": None}
        try:
            h = self._client.sys.read_health_status(method="GET")
            # h can be a dict or a requests.Response depending on sealed state
            if hasattr(h, "json"):
                h = h.json()
            return {
                "enabled": True,
                "connected": True,
                "sealed": h.get("sealed", None),
                "version": h.get("version", None),
            }
        except Exception as exc:
            _log.debug("Vault health check failed: %s", exc)
            return {"enabled": True, "connected": False, "sealed": None, "version": None, "error": str(exc)[:200]}

    def is_available(self) -> bool:
        """Return True if Vault is enabled, connected, and unsealed."""
        if not self._enabled or not self._client:
            return False
        try:
            return self._client.is_authenticated() and not self._client.sys.is_sealed()
        except Exception:
            return False

    # -- KV v2 read / write -------------------------------------------------

    def read(self, key: str, default: str = "") -> str:
        """Read a single secret key from Vault.  Falls back to os.getenv()."""
        if self._enabled and self._client:
            try:
                resp = self._client.secrets.kv.v2.read_secret_version(
                    mount_point=self._KV_MOUNT,
                    path=self._KV_SECRETS_PATH,
                    raise_on_deleted_version=False,
                )
                data = (resp or {}).get("data", {}).get("data", {})
                if key in data:
                    return str(data[key]).strip()
            except Exception as exc:
                _log.debug("Vault read(%s) failed, falling back to env: %s", key, exc)
        return os.getenv(key, default).strip()

    def read_all(self) -> dict:
        """Read all secret key-value pairs from Vault.  Returns {} on failure."""
        if self._enabled and self._client:
            try:
                resp = self._client.secrets.kv.v2.read_secret_version(
                    mount_point=self._KV_MOUNT,
                    path=self._KV_SECRETS_PATH,
                    raise_on_deleted_version=False,
                )
                return dict((resp or {}).get("data", {}).get("data", {}))
            except Exception as exc:
                _log.debug("Vault read_all() failed: %s", exc)
        return {}

    def write(self, entries: dict) -> None:
        """Write secret entries.

        When Vault is enabled: merge into Vault KV v2 + update os.environ.
        When disabled: only update os.environ (in-memory, non-persistent).
        """
        # Always sync to os.environ regardless of backend
        for key, value in entries.items():
            k = str(key).strip().upper()
            v = str(value).strip()
            if v:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

        if not self._enabled or not self._client:
            return

        try:
            existing = self.read_all()
            existing.update({str(k).strip().upper(): str(v).strip() for k, v in entries.items()})
            # Remove keys set to empty string
            existing = {k: v for k, v in existing.items() if v}
            self._client.secrets.kv.v2.create_or_update_secret(
                mount_point=self._KV_MOUNT,
                path=self._KV_SECRETS_PATH,
                secret=existing,
            )
        except Exception as exc:
            _log.error("Vault write failed: %s", exc)
            raise RuntimeError(f"Failed to write secrets to Vault: {exc}") from exc

    def delete(self, keys: list[str]) -> None:
        """Delete specific keys from the secret store."""
        blanks = {k: "" for k in keys}
        # Remove from os.environ
        for k in keys:
            os.environ.pop(str(k).strip().upper(), None)

        if not self._enabled or not self._client:
            return

        try:
            existing = self.read_all()
            for k in keys:
                existing.pop(str(k).strip().upper(), None)
            self._client.secrets.kv.v2.create_or_update_secret(
                mount_point=self._KV_MOUNT,
                path=self._KV_SECRETS_PATH,
                secret=existing,
            )
        except Exception as exc:
            _log.error("Vault delete failed: %s", exc)
            raise RuntimeError(f"Failed to delete secrets from Vault: {exc}") from exc

    # -- PEM handling -------------------------------------------------------

    def read_pem(self) -> Optional[bytes]:
        """Read PEM private key content from Vault.  Returns None on failure."""
        if not self._enabled or not self._client:
            return None
        try:
            resp = self._client.secrets.kv.v2.read_secret_version(
                mount_point=self._KV_MOUNT,
                path=self._KV_PEM_PATH,
                raise_on_deleted_version=False,
            )
            b64 = (resp or {}).get("data", {}).get("data", {}).get("pem_content")
            if b64:
                return base64.b64decode(b64)
        except Exception as exc:
            _log.debug("Vault read_pem() failed: %s", exc)
        return None

    def write_pem(self, content: bytes) -> None:
        """Write PEM private key content to Vault (base64-encoded)."""
        if not self._enabled or not self._client:
            raise RuntimeError("Cannot write PEM: Vault is not enabled/available")
        try:
            b64 = base64.b64encode(content).decode("ascii")
            self._client.secrets.kv.v2.create_or_update_secret(
                mount_point=self._KV_MOUNT,
                path=self._KV_PEM_PATH,
                secret={"pem_content": b64},
            )
        except Exception as exc:
            _log.error("Vault write_pem failed: %s", exc)
            raise RuntimeError(f"Failed to write PEM to Vault: {exc}") from exc

    def has_pem(self) -> bool:
        """Check if a PEM key exists in Vault."""
        if not self._enabled or not self._client:
            return False
        try:
            resp = self._client.secrets.kv.v2.read_secret_version(
                mount_point=self._KV_MOUNT,
                path=self._KV_PEM_PATH,
                raise_on_deleted_version=False,
            )
            return bool((resp or {}).get("data", {}).get("data", {}).get("pem_content"))
        except Exception:
            return False

    # -- startup sync -------------------------------------------------------

    def sync_to_environ(self) -> int:
        """Load all Vault secrets into os.environ.  Returns count of keys synced."""
        if not self._enabled or not self._client:
            return 0
        secrets = self.read_all()
        for k, v in secrets.items():
            if v:
                os.environ[k] = str(v)
        _log.info("Synced %d secret(s) from Vault → os.environ", len(secrets))
        return len(secrets)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

secrets = SecretStore()
