import requests
from github.app_auth import get_installation_token

from github.config import get_github_owner
API_BASE = "https://api.github.com"


def _headers():
    token = get_installation_token()
    return {"Authorization": f"Bearer {token}"}


def get_default_branch(repo):
    owner = get_github_owner()
    r = requests.get(f"{API_BASE}/repos/{owner}/{repo}", headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()["default_branch"]


def get_branch_sha(repo, branch):
    owner = get_github_owner()
    branch_name = str(branch or "").strip()
    if not branch_name:
        return None
    r = requests.get(
        f"{API_BASE}/repos/{owner}/{repo}/git/ref/heads/{branch_name}",
        headers=_headers(), timeout=30
    )
    if r.status_code in {404, 409}:
        # Fallback for branch names that may not resolve reliably via singular ref path.
        m = requests.get(
            f"{API_BASE}/repos/{owner}/{repo}/git/matching-refs/heads/{branch_name}",
            headers=_headers(), timeout=30
        )
        if m.status_code in {404, 409}:
            return None
        m.raise_for_status()
        payload = m.json()
        refs = payload if isinstance(payload, list) else []
        target_ref = f"refs/heads/{branch_name}"
        for item in refs:
            if not isinstance(item, dict):
                continue
            if str(item.get("ref") or "") != target_ref:
                continue
            obj = item.get("object", {}) if isinstance(item.get("object"), dict) else {}
            sha = str(obj.get("sha") or "").strip()
            if sha:
                return sha
        return None
    r.raise_for_status()
    return r.json()["object"]["sha"]


def get_commit(repo, commit_sha):
    owner = get_github_owner()
    r = requests.get(
        f"{API_BASE}/repos/{owner}/{repo}/git/commits/{commit_sha}",
        headers=_headers(), timeout=30
    )
    r.raise_for_status()
    return r.json()


def create_blob(repo, content):
    owner = get_github_owner()
    r = requests.post(
        f"{API_BASE}/repos/{owner}/{repo}/git/blobs",
        headers=_headers(), timeout=30,
        json={
            "content": content,
            "encoding": "utf-8"
        }
    )
    r.raise_for_status()
    return r.json()["sha"]


def create_tree(repo, base_tree_sha, file_path, blob_sha):
    owner = get_github_owner()
    r = requests.post(
        f"{API_BASE}/repos/{owner}/{repo}/git/trees",
        headers=_headers(), timeout=30,
        json={
            "base_tree": base_tree_sha,
            "tree": [
                {
                    "path": file_path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_sha
                }
            ]
        }
    )
    r.raise_for_status()
    return r.json()["sha"]


def create_tree_multi(repo, base_tree_sha, tree_entries):
    payload = {"tree": tree_entries}
    if base_tree_sha:
        payload["base_tree"] = base_tree_sha

    owner = get_github_owner()
    r = requests.post(
        f"{API_BASE}/repos/{owner}/{repo}/git/trees",
        headers=_headers(), timeout=30,
        json=payload
    )
    r.raise_for_status()
    return r.json()["sha"]


def build_tree_from_patchops(repo, base_tree_sha, patch_ops):
    tree_entries = []

    for op in patch_ops:
        action = op["action"]
        path = op["path"]

        if action == "delete":
            tree_entries.append(
                {
                    "path": path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": None,
                }
            )
            continue

        blob_sha = create_blob(repo, op["content"])
        tree_entries.append(
            {
                "path": path,
                "mode": "100644",
                "type": "blob",
                "sha": blob_sha,
            }
        )

    return create_tree_multi(repo, base_tree_sha, tree_entries)


def create_commit(repo, message, tree_sha, parent_sha):
    owner = get_github_owner()
    r = requests.post(
        f"{API_BASE}/repos/{owner}/{repo}/git/commits",
        headers=_headers(), timeout=30,
        json={
            "message": message,
            "tree": tree_sha,
            "parents": [parent_sha] if parent_sha else []
        }
    )
    r.raise_for_status()
    return r.json()["sha"]


def create_branch(repo, branch, base_sha):
    owner = get_github_owner()
    r = requests.post(
        f"{API_BASE}/repos/{owner}/{repo}/git/refs",
        headers=_headers(), timeout=30,
        json={
            "ref": f"refs/heads/{branch}",
            "sha": base_sha
        }
    )
    if r.status_code == 201:
        return

    if r.status_code in {409, 422}:
        existing_sha = get_branch_sha(repo, branch)
        if existing_sha:
            return
    r.raise_for_status()


def create_or_update_branch(repo, branch, base_sha):
    current_sha = get_branch_sha(repo, branch)
    if current_sha:
        return current_sha

    create_branch(repo, branch, base_sha)
    return base_sha


def update_branch(repo, branch, commit_sha):
    # IMPORTANT: no force
    owner = get_github_owner()
    r = requests.patch(
        f"{API_BASE}/repos/{owner}/{repo}/git/refs/heads/{branch}",
        headers=_headers(), timeout=30,
        json={
            "sha": commit_sha
        }
    )
    r.raise_for_status()
