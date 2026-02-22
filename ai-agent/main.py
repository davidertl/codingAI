import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone

from core.test_runner import analyze_repo, run_tests
from github.checks_manager import create_completed_check_run
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
from llm.patch_llm import propose_patch_ops, propose_test_patch_ops

AVAILABLE_REPOS = [
    "KRT-leadtool",
    "KRT-Com_Discord",
]
TARGET_REPOS_ENV = os.getenv("TARGET_REPOS", "").strip()
if TARGET_REPOS_ENV:
    AVAILABLE_REPOS = [r.strip() for r in TARGET_REPOS_ENV.split(",") if r.strip()]

STATE_FILE = "/home/codingai/ai-agent/state.json"
POLL_INTERVAL = 300
FAIL_COOLDOWN_SECONDS = 6 * 60 * 60  # 6h
MAX_PATCH_OPS = 20
MAX_TEST_PATCH_OPS = int(os.getenv("MAX_TEST_PATCH_OPS", "6"))
MAX_TOTAL_PATCH_OPS = int(os.getenv("MAX_TOTAL_PATCH_OPS", "30"))
AUTO_GENERATE_TEST_PATCHES = os.getenv("AUTO_GENERATE_TEST_PATCHES", "false").strip().lower() in {"1", "true", "yes", "on"}
REPORT_MARKER = "<!-- codingai-test-report -->"
AI_STOP_PHRASE = "AI Stop"
STRATEGY_SWITCH_CONFIDENCE_THRESHOLD = float(os.getenv("STRATEGY_SWITCH_CONFIDENCE_THRESHOLD", "0.65"))
MAX_PRS_PER_REPO_PER_DAY = int(os.getenv("MAX_PRS_PER_REPO_PER_DAY", "3"))
MANUAL_APPROVAL_REQUIRED = (os.getenv("MANUAL_APPROVAL_REQUIRED", "false").strip().lower() in {"1", "true", "yes", "on"})
PR_AUTO_UPDATE_ENABLED = (os.getenv("PR_AUTO_UPDATE_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"})
MANUAL_APPROVAL_LABELS = {
    s.strip().lower()
    for s in os.getenv("MANUAL_APPROVAL_LABELS", "ai-approve,ai-approved,manual-approval-granted").split(",")
    if s.strip()
}
MANUAL_APPROVAL_TOKEN = os.getenv("MANUAL_APPROVAL_TOKEN", "[ai-approve]").strip().lower()
ENABLE_GITHUB_CHECKS = os.getenv("ENABLE_GITHUB_CHECKS", "true").strip().lower() in {"1", "true", "yes", "on"}
CHECK_RUN_NAME = os.getenv("CHECK_RUN_NAME", "CodingAI Local Validation")
RUN_ALL_REPOS = os.getenv("RUN_ALL_REPOS", "false").strip().lower() in {"1", "true", "yes", "on"}


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r") as f:
        return json.load(f)


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def choose_repo():
    if RUN_ALL_REPOS:
        return "__all__"

    print("Select repository to work on:")
    for idx, repo in enumerate(AVAILABLE_REPOS):
        print(f"{idx + 1}. {repo}")
    print("a. all repositories")

    choice = input("Enter number: ").strip().lower()

    if choice in {"a", "all", "*"}:
        return "__all__"

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


def _get_issue_state(state, repo, number):
    return state.setdefault(repo, {}).setdefault(str(number), {})


def _register_failure(state, repo, number, summary, details):
    details = (details or "").strip()
    details = details[:12000]

    try:
        create_issue(
            repo,
            f"AI Failure for Issue #{number}",
            f"{summary}\n\n```\n{details}\n```",
        )
    except Exception as e:
        print(f"Failed to publish failure issue for {repo}#{number}: {e}")

    issue_state = _get_issue_state(state, repo, number)
    issue_state["pr_created"] = bool(issue_state.get("pr_created", False))
    issue_state["last_status"] = "failed"
    issue_state["last_error"] = details[:2000]
    issue_state["retry_after"] = int(time.time()) + FAIL_COOLDOWN_SECONDS
    save_state(state)


def _apply_strategy_memory_update(state, repo, test_report):
    memory_update = test_report.get("strategy_memory_update")
    if not isinstance(memory_update, dict):
        return
    state.setdefault("strategy_memory", {})[repo] = memory_update


def _mark_ai_stopped(state, repo, issue_number, pr_number, comment_id):
    now = int(time.time())
    issue_state = _get_issue_state(state, repo, issue_number)
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
    issue_state = _get_issue_state(state, repo, issue_number)
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
        issue_state["pr_created"] = True
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


def _extract_issue_labels(issue):
    out = set()
    for l in issue.get("labels", []):
        if isinstance(l, dict):
            name = str(l.get("name", "")).strip()
        else:
            name = str(l).strip()
        if name:
            out.add(name.lower())
    return out


def _has_manual_approval(issue):
    if not MANUAL_APPROVAL_REQUIRED:
        return True

    labels = _extract_issue_labels(issue)
    if labels.intersection(MANUAL_APPROVAL_LABELS):
        return True

    text = f"{issue.get('title', '')}\n{issue.get('body', '')}".lower()
    if MANUAL_APPROVAL_TOKEN and MANUAL_APPROVAL_TOKEN in text:
        return True
    if "ai approve" in text:
        return True

    return False


def _utc_day_key():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _seconds_until_next_utc_day():
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60, int((tomorrow - now).total_seconds()))


def _get_daily_pr_count(state, repo, day_key):
    return int(state.get("daily_pr_counts", {}).get(repo, {}).get(day_key, 0))


def _increment_daily_pr_count(state, repo, day_key):
    state.setdefault("daily_pr_counts", {}).setdefault(repo, {})
    current = int(state["daily_pr_counts"][repo].get(day_key, 0))
    state["daily_pr_counts"][repo][day_key] = current + 1


def _resolve_branch_for_issue(state, repo, issue_number):
    issue_state = _get_issue_state(state, repo, issue_number)
    active_branch = issue_state.get("active_branch")

    if active_branch:
        pr_info = get_open_pr_for_branch(repo, active_branch)
        if pr_info:
            issue_state["pr_created"] = True
            issue_state["pr_number"] = pr_info["number"]
            issue_state["pr_url"] = pr_info["url"]
            return active_branch, pr_info

        # previous PR for active branch is not open anymore -> start a new iteration branch
        if issue_state.get("pr_created"):
            issue_state["pr_created"] = False
            issue_state["pr_number"] = None
            issue_state["pr_url"] = None
            issue_state["report_comment_id"] = None
            issue_state["source_issue_updated_at"] = None
            issue_state["active_branch"] = None
            active_branch = None

    if not active_branch:
        next_iter = int(issue_state.get("branch_iteration", 0)) + 1
        active_branch = f"ai/issue-{int(issue_number)}-iter-{next_iter}"
        issue_state["branch_iteration"] = next_iter
        issue_state["active_branch"] = active_branch

    return active_branch, None


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


def _merge_patch_ops(base_ops, extra_ops, *, max_ops):
    merged = list(base_ops)
    seen_paths = {op["path"] for op in merged}
    added = 0

    for op in extra_ops:
        if len(merged) >= max_ops:
            break
        path = op["path"]
        if path in seen_paths:
            continue
        merged.append(op)
        seen_paths.add(path)
        added += 1

    return merged, added


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
    test_patch_ops_added = int(patch_result.get("test_patch_ops_added", 0))

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
        f"- Patch confidence: `{patch_conf:.2f}`\n"
        f"- Auto-generated test patch ops: `{test_patch_ops_added}`\n\n"
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


def _build_check_run_output(issue_number, test_report, test_output, patch_result, patch_ops_count):
    attempts = test_report.get("attempts", [])
    attempts_text = []
    for a in attempts:
        attempts_text.append(
            f"- attempt {a.get('attempt')}: {a.get('strategy_id')} -> {a.get('result')} "
            f"({a.get('error_fingerprint') or 'ok'})"
        )
    attempts_block = "\n".join(attempts_text) if attempts_text else "- no attempts recorded"

    selected = test_report.get("selected_strategy") or "n/a"
    selected_conf = float(test_report.get("selected_strategy_confidence", 0.0))
    patch_conf = float(patch_result.get("confidence", 0.0))
    test_patch_ops_added = int(patch_result.get("test_patch_ops_added", 0))
    out = (test_output or "").strip()[:2000] or "No output captured."

    title = "CodingAI validation passed"
    summary = (
        f"Issue #{issue_number}: tests passed after patch generation. "
        f"Patch ops={patch_ops_count}, patch_conf={patch_conf:.2f}, "
        f"test_ops_added={test_patch_ops_added}, "
        f"selected_strategy={selected}, strategy_conf={selected_conf:.2f}."
    )
    text = (
        "### Strategy Attempts\n"
        f"{attempts_block}\n\n"
        "### Last Test Output (truncated)\n"
        "```text\n"
        f"{out}\n"
        "```"
    )
    return title, summary, text


def process_issue(repo, issue, state):
    number = str(issue["number"])
    issue_state = _get_issue_state(state, repo, number)

    # resolve active/open PR branch first (iteration naming strategy)
    try:
        branch, existing_pr = _resolve_branch_for_issue(state, repo, number)

        if _sync_ai_stop_state(state, repo, number, branch):
            print(f"Issue #{number} has AI Stop on PR comments. Processing disabled.")
            return
    except Exception as e:
        _register_failure(
            state,
            repo,
            number,
            f"Phase 5 preflight failed for issue #{number}.",
            str(e),
        )
        return

    # keep state synced if an open PR exists for this branch
    if existing_pr:
        issue_state["pr_created"] = True
        issue_state["pr_number"] = existing_pr["number"]
        issue_state["pr_url"] = existing_pr["url"]

    if not _has_manual_approval(issue):
        issue_state["pending_manual_approval"] = True
        issue_state["last_status"] = "pending_manual_approval"
        save_state(state)
        print(f"Issue #{number} pending manual approval. Skipping.")
        return
    issue_state["pending_manual_approval"] = False

    # Existing PR behavior: either skip completely or auto-update only when issue changed.
    if issue_state.get("pr_created"):
        if not PR_AUTO_UPDATE_ENABLED:
            print(f"Issue #{number} has existing PR and auto-update is disabled. Skipping.")
            return

        issue_updated_at = str(issue.get("updated_at", ""))
        if issue_updated_at and issue_updated_at == str(issue_state.get("source_issue_updated_at", "")):
            print(f"Issue #{number} unchanged since last run; skipping PR auto-update.")
            return

    # Cooldown
    if should_skip_due_to_cooldown(state, repo, number):
        print(f"Issue #{number} in cooldown. Skipping.")
        return

    # Daily safety cap for creating NEW PRs
    if not issue_state.get("pr_created") and MAX_PRS_PER_REPO_PER_DAY > 0:
        day_key = _utc_day_key()
        daily_count = _get_daily_pr_count(state, repo, day_key)
        if daily_count >= MAX_PRS_PER_REPO_PER_DAY:
            issue_state["last_status"] = "daily_pr_limit_reached"
            issue_state["retry_after"] = int(time.time()) + _seconds_until_next_utc_day()
            save_state(state)
            print(
                f"Repo {repo} reached daily PR cap ({daily_count}/{MAX_PRS_PER_REPO_PER_DAY}) on {day_key}. Skipping."
            )
            return

    print(f"\nProcessing issue #{number} on branch {branch}")

    repo_path = clone_or_update(repo)

    try:
        default_branch = get_default_branch(repo)
        branch_sha = get_branch_sha(repo, branch)
        base_sha = branch_sha or get_branch_sha(repo, default_branch)
        repo_strategy_memory = state.get("strategy_memory", {}).get(repo, {})

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
        patch_result["test_patch_ops_added"] = 0

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

        if AUTO_GENERATE_TEST_PATCHES:
            test_patch_result = propose_test_patch_ops(
                repo_path=repo_path,
                repo_name=repo,
                issue=issue,
                repo_analysis=repo_analysis,
                base_patch_ops=patch_ops,
                max_ops=MAX_TEST_PATCH_OPS,
            )
            test_patch_ops = test_patch_result.get("patch_ops", [])
            if test_patch_ops:
                test_patch_conf = float(test_patch_result.get("confidence", 0.0) or 0.0)
                patch_ops, added = _merge_patch_ops(
                    patch_ops,
                    test_patch_ops,
                    max_ops=MAX_TOTAL_PATCH_OPS,
                )
                patch_result["test_patch_ops_added"] = added
                patch_result["test_patch_confidence"] = test_patch_conf
                patch_result["test_patch_reason"] = test_patch_result.get("reason", "")
                print(
                    f"Auto-generated test patch ops added: {added} "
                    f"(candidate={len(test_patch_ops)} conf={test_patch_conf:.2f})"
                )
            else:
                print(f"No test patch ops generated: {test_patch_result.get('reason', '')}")

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
        success, output, test_report = run_tests(
            repo_path,
            repo_name=repo,
            max_attempts=3,
            strategy_memory=repo_strategy_memory,
            min_confidence_for_switch=STRATEGY_SWITCH_CONFIDENCE_THRESHOLD,
        )
        _apply_strategy_memory_update(state, repo, test_report)

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

        if pr_info.get("created"):
            _increment_daily_pr_count(state, repo, _utc_day_key())

        print("PR URL:", pr_info["url"])

        check_run_url = None
        if ENABLE_GITHUB_CHECKS:
            try:
                check_title, check_summary, check_text = _build_check_run_output(
                    issue_number=number,
                    test_report=test_report,
                    test_output=output,
                    patch_result=patch_result,
                    patch_ops_count=len(patch_ops),
                )
                check_run = create_completed_check_run(
                    repo,
                    name=CHECK_RUN_NAME,
                    head_sha=commit_sha,
                    conclusion="success",
                    title=check_title,
                    summary=check_summary,
                    text=check_text,
                    details_url=pr_info["url"],
                    external_id=f"{repo}#{number}",
                )
                check_run_url = check_run.get("html_url")
                print(f"Check run published: {check_run_url}")
            except Exception as check_err:
                print(f"Check run publish failed: {check_err}")

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

        issue_state["active_branch"] = branch
        issue_state["pr_created"] = True
        issue_state["last_status"] = "passed"
        issue_state["patch_ops_count"] = len(patch_ops)
        issue_state["patch_confidence"] = patch_result.get("confidence", 0.0)
        issue_state["strategy_confidence_threshold"] = STRATEGY_SWITCH_CONFIDENCE_THRESHOLD
        issue_state["pr_number"] = pr_info["number"]
        issue_state["pr_url"] = pr_info["url"]
        issue_state["check_run_url"] = check_run_url
        issue_state["report_comment_id"] = comment_result.get("id")
        issue_state["source_issue_updated_at"] = str(issue.get("updated_at", ""))
        issue_state["retry_after"] = 0
        save_state(state)

    except Exception as e:
        _register_failure(
            state,
            repo,
            number,
            f"Patch pipeline failed for issue #{number}.",
            str(e),
        )


def _run_repo_cycle(repo):
    state = load_state()
    try:
        issues = get_ai_issues(repo)
    except Exception as e:
        print(f"Failed to fetch issues for {repo}: {e}")
        return

    for issue in issues:
        process_issue(repo, issue, state)


def loop(repo):
    while True:
        _run_repo_cycle(repo)
        time.sleep(POLL_INTERVAL)


def loop_all(repos):
    while True:
        for repo in repos:
            print(f"\n== Repo cycle: {repo} ==")
            _run_repo_cycle(repo)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    selected = choose_repo()
    if selected == "__all__":
        print(f"\nAI Agent active for all repos: {', '.join(AVAILABLE_REPOS)}")
        loop_all(AVAILABLE_REPOS)
    else:
        print(f"\nAI Agent active for {selected}")
        loop(selected)
