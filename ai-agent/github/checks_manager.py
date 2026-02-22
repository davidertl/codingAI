from datetime import datetime, timezone

import requests

from github.app_auth import get_installation_token

OWNER = "davidertl"


def _headers():
    token = get_installation_token()
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }


def _utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_completed_check_run(
    repo,
    *,
    name,
    head_sha,
    conclusion,
    title,
    summary,
    text=None,
    details_url=None,
    external_id=None,
):
    payload = {
        "name": name,
        "head_sha": head_sha,
        "status": "completed",
        "completed_at": _utc_now_iso(),
        "conclusion": conclusion,
        "output": {
            "title": title[:255],
            "summary": summary[:65535],
        },
    }
    if text:
        payload["output"]["text"] = text[:65535]
    if details_url:
        payload["details_url"] = details_url
    if external_id:
        payload["external_id"] = str(external_id)[:255]

    r = requests.post(
        f"https://api.github.com/repos/{OWNER}/{repo}/check-runs",
        headers=_headers(),
        json=payload,
    )
    r.raise_for_status()
    return r.json()
