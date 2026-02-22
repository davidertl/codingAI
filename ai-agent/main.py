import json
import os
import subprocess
import time
from datetime import datetime, timezone

from core.test_runner import analyze_repo, run_tests
from github.git_api_commit import (
    build_tree_from_patchops,
    create_branch,
    create_commit,
    get_branch_sha,
    get_commit,
    get_default_branch,
    update_branch,
)
from github.issue_manager import create_issue, get_ai_issues
from github.pr_manager import (
    create_or_get_pr,
    get_open_pr_for_branch,
    has_ai_stop_comment,
    upsert_pr_comment,
)
from github.repo_manager import clone_or_update
from llm.patch_llm import propose_patch_ops

AVAILABLE_REPOS = [
    "KRT-leadtool",
    "KRT-Com_Discord",
]

STATE_FILE = "/home/codingai/ai-agent/state.json"
POLL_INTERVAL = 300
FAIL_COOLDOWN_SECONDS = 6 * 60 * 60  # 6h
MAX_PATCH_OPS = 20
REPORT_MARKER = "<!-- codingai-test-report -->"
AI_STOP_PHRASE = "AI Stop"


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


def _register_failure(state, repo, number, summary, details):
    details = (details or "").strip()
    details = details[:12000]

    create_issue(
        repo,
        f"AI Failure for Issue #{number}",
        f"{summary}\n\n```\n{details}\n```",
    )

    state[repo][number] = {
        "pr_created": False,
        "last_status": "failed",
        "last_error": details[:2000],
        "retry_after": int(time.time()) + FAIL_COOLDOWN_SECONDS,
    }
    save_state(state)


def _mark_ai_stopped(state, repo, issue_number, pr_number, comment_id):
    now = int(time.time())
    issue_state = state.setdefault(repo, {}).setdefault(issue_number, {})
    issue_state["ai_stopped"] = True
    issue_state["ai_stopped_at"] = now
    issue_state["ai_stop_reason"] = AI_STOP_PHRASE
    issue_state["pr_number"] = pr_number
    issue_state["ai_stop_comment_id"] = comment_id

    state.setdefault("pr_controls", {})[str(pr_number)] = {
        "ai_stopped": True,
        "stopped_at": now,
        "reason": AI_STOP_PHRASE,
    }


def _sync_ai_stop_state(state, repo, issue_number, branch):
    issue_state = state.setdefault(repo, {}).setdefault(issue_number, {})
    if issue_state.get("ai_stopped"):
        return True

    pr_number = issue_state.get("pr_number")
    if not pr_number:
        pr_info = get_open_pr_for_branch(repo, branch)
        if not pr_info:
            return False
        pr_number = pr_info["number"]
        issue_state["pr_number"] = pr_number
        issue_state["pr_url"] = pr_info["url"]
        save_state(state)

    stopped, comment = has_ai_stop_comment(repo, pr_number, phrase=AI_STOP_PHRASE)
    if not stopped:
        return False

    _mark_ai_stopped(
        state=state,
        repo=repo,
        issue_number=issue_number,
        pr_number=pr_number,
        comment_id=comment.get("id") if comment else None,
    )
    save_state(state)
    return True


def _checkout_local_branch_at_sha(repo_path, branch, base_sha):
    subprocess.run(["git", "checkout", "-B", branch, base_sha], cwd=repo_path, check=True)
    subprocess.run(["git", "reset", "--hard", base_sha], cwd=repo_path, check=True)


def _is_within_repo(repo_path, target_path):
    repo_real = os.path.realpath(repo_path)
    target_real = os.path.realpath(target_path)
    return target_real == repo_real or target_real.startswith(repo_real + os.sep)


def _apply_patch_ops_locally(repo_path, patch_ops):
    for op in patch_ops:
        rel_path = op["path"]
        action = op["action"]
        target_path = os.path.join(repo_path, rel_path)

        if not _is_within_repo(repo_path, target_path):
            raise ValueError(f"Unsafe patch path: {rel_path}")

        if action == "delete":
            if os.path.isdir(target_path):
                raise ValueError(f"Delete action points to directory: {rel_path}")
            if os.path.exists(target_path):
                os.remove(target_path)
            continue

        parent = os.path.dirname(target_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        with open(target_path, "w", encoding="utf-8", errors="ignore") as f:
            f.write(op["content"])


def _local_repo_has_changes(repo_path):
    r = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return bool(r.stdout.strip())


def _build_pr_test_comment(issue_number, test_report, test_output, patch_result, patch_ops_count):
    attempts = test_report.get("attempts", [])
    if attempts:
        attempt_lines = []
        for a in attempts:
            icon = "✅" if a.get("result") == "passed" else "❌"
            fp = a.get("error_fingerprint")
            fp_text = f" (fingerprint: `{fp}`)" if fp else ""
            attempt_lines.append(
                f"- {icon} Attempt {a.get('attempt')}: `{a.get('strategy_id')}` - {a.get('result')}{fp_text}"
            )
        attempts_md = "\n".join(attempt_lines)
    else:
        attempts_md = "- No attempts recorded"

    selected_strategy = test_report.get("selected_strategy") or "n/a"
    selected_reason = test_report.get("selected_strategy_reason") or "n/a"
    selected_conf = float(test_report.get("selected_strategy_confidence", 0.0))
    patch_conf = float(patch_result.get("confidence", 0.0))

    result = str(test_report.get("result", "failed")).upper()
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    out = (test_output or "").strip()
    out = out[:1800] if out else "No output captured."

    return (
        f"{REPORT_MARKER}\n"
        "## CodingAI Test Report\n\n"
        f"- Issue: `#{issue_number}`\n"
        f"- Generated (UTC): `{now_utc}`\n"
        f"- Result: **{result}**\n"
        f"- Patch operations: `{patch_ops_count}`\n"
        f"- Patch confidence: `{patch_conf:.2f}`\n\n"
        "### Strategy Attempts\n"
        f"{attempts_md}\n\n"
        "### Selected Strategy\n"
        f"- Strategy: `{selected_strategy}`\n"
        f"- Confidence: `{selected_conf:.2f}`\n"
        f"- Reason: {selected_reason}\n\n"
        "### Last Test Output (truncated)\n"
        "```text\n"
        f"{out}\n"
        "```\n"
    )


def process_issue(repo, issue, state):
    number = str(issue["number"])
    branch = f"ai/issue-{int(number)}"

    if repo not in state:
        state[repo] = {}

    if _sync_ai_stop_state(state, repo, number, branch):
        print(f"Issue #{number} has AI Stop on PR comments. Processing disabled.")
        return

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

    try:
        default_branch = get_default_branch(repo)
        branch_sha = get_branch_sha(repo, branch)
        base_sha = branch_sha or get_branch_sha(repo, default_branch)

        if not base_sha:
            raise RuntimeError("Could not resolve base SHA for patch pipeline.")

        # Ensure local tests run against the exact commit parent we will use for API commit.
        _checkout_local_branch_at_sha(repo_path, branch, base_sha)

        repo_analysis = analyze_repo(repo_path)
        patch_result = propose_patch_ops(
            repo_path=repo_path,
            repo_name=repo,
            issue=issue,
            repo_analysis=repo_analysis,
            max_ops=MAX_PATCH_OPS,
        )
        patch_ops = patch_result.get("patch_ops", [])

        if not patch_ops:
            reason = patch_result.get("reason", "Model returned no patch operations.")
            _register_failure(
                state,
                repo,
                number,
                f"Patch generation failed for issue #{number}.",
                reason,
            )
            return

        _apply_patch_ops_locally(repo_path, patch_ops)

        if not _local_repo_has_changes(repo_path):
            _register_failure(
                state,
                repo,
                number,
                f"Patch ops produced no effective file changes for issue #{number}.",
                patch_result.get("reason", ""),
            )
            return

        print("Patch applied locally. Running tests on patched repository...")
        success, output, test_report = run_tests(repo_path, repo_name=repo, max_attempts=3)

        if not success:
            _register_failure(
                state,
                repo,
                number,
                f"Patched repository failed tests for issue #{number}.",
                output,
            )
            return

        print("Patched tests passed. Creating commit via GitHub API...")

        # Create branch remotely only after patch and tests passed.
        if not branch_sha:
            create_branch(repo, branch, base_sha)

        base_commit = get_commit(repo, base_sha)
        base_tree_sha = base_commit["tree"]["sha"]
        tree_sha = build_tree_from_patchops(repo, base_tree_sha, patch_ops)

        commit_sha = create_commit(
            repo,
            f"AI patch for issue #{number} ({len(patch_ops)} files)",
            tree_sha,
            base_sha,
        )

        update_branch(repo, branch, commit_sha)

        pr_info = create_or_get_pr(repo, branch, int(number))
        if not pr_info:
            _register_failure(
                state,
                repo,
                number,
                f"PR creation failed for issue #{number}.",
                "Commit was created and branch updated, but PR creation returned no result.",
            )
            return

        print("PR URL:", pr_info["url"])

        comment_body = _build_pr_test_comment(
            issue_number=number,
            test_report=test_report,
            test_output=output,
            patch_result=patch_result,
            patch_ops_count=len(patch_ops),
        )
        comment_result = upsert_pr_comment(
            repo,
            pr_info["number"],
            comment_body,
            marker=REPORT_MARKER,
        )
        action = "updated" if comment_result.get("updated") else "created"
        print(f"PR report comment {action}: {comment_result.get('url')}")

        state[repo][number] = {
            "pr_created": True,
            "last_status": "passed",
            "patch_ops_count": len(patch_ops),
            "patch_confidence": patch_result.get("confidence", 0.0),
            "pr_number": pr_info["number"],
            "pr_url": pr_info["url"],
            "report_comment_id": comment_result.get("id"),
            "retry_after": 0,
        }
        save_state(state)

    except Exception as e:
        _register_failure(
            state,
            repo,
            number,
            f"Patch pipeline failed for issue #{number}.",
            str(e),
        )


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
