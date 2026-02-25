import jwt
import time
import requests
import os

from core.secret_store import secrets as secret_store
from paths import GITHUB_APP_PEM_FILE

PRIVATE_KEY_PATH = str(GITHUB_APP_PEM_FILE)

_cached_token = None
_token_expiry = 0


def _config_value(key: str) -> str:
    """Read a config/secret value.  Vault → os.environ fallback."""
    return secret_store.read(key)


def _generate_jwt():
    # Try reading PEM from Vault first, then fall back to disk
    pem_content = secret_store.read_pem()
    if pem_content:
        private_key = pem_content.decode("utf-8")
    else:
        with open(PRIVATE_KEY_PATH, "r") as f:
            private_key = f.read()

    app_id = _config_value("GITHUB_APP_ID")
    if not app_id:
        raise Exception("Missing GITHUB_APP_ID in environment or Vault")

    payload = {
        "iat": int(time.time()) - 60,
        "exp": int(time.time()) + (10 * 60),
        "iss": app_id,
    }

    return jwt.encode(payload, private_key, algorithm="RS256")


def get_installation_token():
    global _cached_token, _token_expiry

    if _cached_token and time.time() < _token_expiry - 60:
        return _cached_token

    jwt_token = _generate_jwt()
    installation_id = _config_value("GITHUB_INSTALLATION_ID")
    if not installation_id:
        raise Exception("Missing GITHUB_INSTALLATION_ID in environment or .env")

    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json",
    }

    url = f"https://api.github.com/app/installations/{int(installation_id)}/access_tokens"

    r = requests.post(url, headers=headers, timeout=30)

    if r.status_code != 201:
        raise Exception(f"Token creation failed: {r.text}")

    data = r.json()

    _cached_token = data["token"]

    expires_at = data["expires_at"]
    _token_expiry = int(time.mktime(time.strptime(expires_at, "%Y-%m-%dT%H:%M:%SZ")))

    return _cached_token
