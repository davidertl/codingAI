import jwt
import time
import requests
import os
from dotenv import load_dotenv

load_dotenv("/home/codingai/ai-agent/.env")

APP_ID = os.getenv("GITHUB_APP_ID")
INSTALLATION_ID = os.getenv("GITHUB_INSTALLATION_ID")
PRIVATE_KEY_PATH = "/home/codingai/ai-agent/github_app/KRT-AI-Agent.pem"

_cached_token = None
_token_expiry = 0


def _generate_jwt():
    with open(PRIVATE_KEY_PATH, "r") as f:
        private_key = f.read()

    payload = {
        "iat": int(time.time()) - 60,
        "exp": int(time.time()) + (10 * 60),
        "iss": APP_ID,
    }

    return jwt.encode(payload, private_key, algorithm="RS256")


def get_installation_token():
    global _cached_token, _token_expiry

    if _cached_token and time.time() < _token_expiry - 60:
        return _cached_token

    jwt_token = _generate_jwt()

    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json",
    }

    url = f"https://api.github.com/app/installations/{int(INSTALLATION_ID)}/access_tokens"

    r = requests.post(url, headers=headers)

    if r.status_code != 201:
        raise Exception(f"Token creation failed: {r.text}")

    data = r.json()

    _cached_token = data["token"]

    expires_at = data["expires_at"]
    _token_expiry = int(time.mktime(time.strptime(expires_at, "%Y-%m-%dT%H:%M:%SZ")))

    return _cached_token