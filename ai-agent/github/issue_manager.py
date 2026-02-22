import requests
from github.app_auth import get_installation_token

OWNER = "davidertl"


def get_ai_issues(repo):
    token = get_installation_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json"
    }

    url = f"https://api.github.com/repos/{OWNER}/{repo}/issues"
    params = {"state": "open", "labels": "ai-fix"}

    r = requests.get(url, headers=headers, params=params)

    return r.json() if r.status_code == 200 else []


def create_issue(repo, title, body):
    token = get_installation_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json"
    }

    url = f"https://api.github.com/repos/{OWNER}/{repo}/issues"

    data = {"title": title, "body": body}

    requests.post(url, headers=headers, json=data)
