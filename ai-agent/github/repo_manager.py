import os
import subprocess
from github.app_auth import get_installation_token

GITHUB_OWNER = "davidertl"
WORKSPACE_ROOT = "/home/codingai/workspaces"


def clone_or_update(repo_name):
    repo_path = f"{WORKSPACE_ROOT}/{repo_name}"

    if os.path.exists(repo_path):
        subprocess.run(["git", "fetch"], cwd=repo_path)
        subprocess.run(["git", "reset", "--hard", "origin/main"], cwd=repo_path)
    else:
        subprocess.run(
            ["git", "clone", f"https://github.com/{GITHUB_OWNER}/{repo_name}.git"],
            cwd=WORKSPACE_ROOT,
            check=True
        )

    return repo_path


def create_ai_branch(repo_path, issue_number):
    branch_name = f"ai/issue-{issue_number}"
    subprocess.run(["git", "checkout", "-B", branch_name], cwd=repo_path, check=True)
    return branch_name


def commit_all(repo_path, message):
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo_path)


def push_branch(repo_path, branch):
    print("Acquiring GitHub App installation token for push...")
    token = get_installation_token()

    if not token:
        raise Exception("Failed to acquire installation token.")

    remote_url = f"https://x-access-token:{token}@github.com/{GITHUB_OWNER}/{os.path.basename(repo_path)}.git"

    # Set authenticated remote temporarily
    subprocess.run(["git", "remote", "set-url", "origin", remote_url], cwd=repo_path, check=True)

    # Push
    subprocess.run(
        ["git", "push", "-u", "origin", branch, "--force"],
        cwd=repo_path,
        check=True
    )

    # Restore clean remote URL (no token stored)
    clean_url = f"https://github.com/{GITHUB_OWNER}/{os.path.basename(repo_path)}.git"
    subprocess.run(["git", "remote", "set-url", "origin", clean_url], cwd=repo_path, check=True)

    print("Push successful.")