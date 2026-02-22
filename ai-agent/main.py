import time
import json
import os

from github.issue_manager import get_ai_issues, create_issue
from github.repo_manager import clone_or_update, create_ai_branch
from github.pr_manager import create_or_get_pr
from github.git_api_commit import (
    get_default_branch,
    get_branch_sha,
    create_blob,
    create_tree,
    create_commit,
    update_branch,
    create_or_update_branch
)
from core.test_runner import run_tests

AVAILABLE_REPOS = [
    "KRT-leadtool",
    "KRT-Com_Discord"
]

STATE_FILE = "/home/codingai/ai-agent/state.json"
POLL_INTERVAL = 300
FAIL_COOLDOWN_SECONDS = 6 * 60 * 60  # 6h


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r") as f:
        return json.load(f)


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def choose_repo():
    print("Select repository to work on:")
    for idx, repo in enumerate(AVAILABLE_REPOS):
        print(f"{idx + 1}. {repo}")

    choice = input("Enter number: ").strip()

    if not choice.isdigit():
        print("Invalid selection (not a number).")
        exit(1)

    n = int(choice)
    if n < 1 or n > len(AVAILABLE_REPOS):
        print("Invalid selection (out of range).")
        exit(1)

    return AVAILABLE_REPOS[n - 1]


def should_skip_due_to_cooldown(state, repo, issue_number):
    repo_state = state.get(repo, {})
    issue_state = repo_state.get(str(issue_number), {})
    retry_after = issue_state.get("retry_after", 0)
    now = int(time.time())
    return now < retry_after


def process_issue(repo, issue, state):
    number = str(issue["number"])

    if repo not in state:
        state[repo] = {}

    # Already processed
    if number in state[repo] and state[repo][number].get("pr_created"):
        print(f"Issue #{number} already processed (PR created). Skipping.")
        return

    # Cooldown
    if should_skip_due_to_cooldown(state, repo, number):
        print(f"Issue #{number} in cooldown. Skipping.")
        return

    print(f"\nProcessing issue #{number}")

    repo_path = clone_or_update(repo)
    branch = create_ai_branch(repo_path, int(number))

    # Temporary local change (will be committed via API)
    with open(f"{repo_path}/AI_CHANGE.txt", "a") as f:
        f.write(f"Handled issue {number}\n")

    success, output = run_tests(repo_path, repo_name=repo, max_attempts=3)

    if success:
        print("Tests passed. Creating commit via GitHub API...")

        default_branch = get_default_branch(repo)

        branch_sha = get_branch_sha(repo, branch)

        if branch_sha:
            base_sha = branch_sha
        else:
            base_sha = get_branch_sha(repo, default_branch)
            create_branch(repo, branch, base_sha)

        blob_sha = create_blob(repo, f"Handled issue {number}\n")
        tree_sha = create_tree(repo, base_sha, "AI_CHANGE.txt", blob_sha)

        commit_sha = create_commit(
            repo,
            f"AI attempt for issue #{number}",
            tree_sha,
            base_sha
        )

        update_branch(repo, branch, commit_sha)

        pr_url = create_or_get_pr(repo, branch, int(number))
        print("PR URL:", pr_url)

        state[repo][number] = {
            "pr_created": True,
            "last_status": "passed",
            "retry_after": 0
        }
        save_state(state)

    else:
        print("Tests failed.")
        print(output)

        create_issue(
            repo,
            f"AI Test Failure for Issue #{number}",
            f"Adaptive test runner exhausted strategies.\n\n```\n{output}\n```"
        )

        state[repo][number] = {
            "pr_created": False,
            "last_status": "failed",
            "last_error": output[:2000],
            "retry_after": int(time.time()) + FAIL_COOLDOWN_SECONDS
        }
        save_state(state)


def loop(repo):
    while True:
        state = load_state()
        issues = get_ai_issues(repo)

        for issue in issues:
            process_issue(repo, issue, state)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    selected_repo = choose_repo()
    print(f"\nAI Agent active for {selected_repo}")
    loop(selected_repo)