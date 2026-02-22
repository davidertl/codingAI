import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from core.observability import inc_counter, observe_duration_ms, record_event, set_gauge
from core.policy import get_policy_snapshot as load_policy_snapshot
from core.policy import get_repo_policy
from core.test_runner import analyze_repo, run_tests
from github.checks_manager import create_completed_check_run
from github.ci_status import get_pr_ci_status
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
from github.repo_manager import clone_or_update, prepare_job_worktree, cleanup_jobs, disk_usage_report
from llm.provider import ensure_llm_ready, get_llm_runtime_status
from llm.patch_llm import propose_patch_ops, propose_test_patch_ops
from paths import ENV_FILE, STATE_FILE

AVAILABLE_REPOS = [
    "KRT-leadtool",
    "KRT-Com_Discord",
]
TARGET_REPOS_ENV = os.getenv("TARGET_REPOS", "").strip()
if TARGET_REPOS_ENV:
    AVAILABLE_REPOS = [r.strip() for r in TARGET_REPOS_ENV.split(",") if r.strip()]

POLL_INTERVAL = 300
REPORT_MARKER = "<!-- codingai-test-report -->"
RUN_ALL_REPOS = os.getenv("RUN_ALL_REPOS", "false").strip().lower() in {"1", "true", "yes", "on"}

PAUSE_WINDOW_SECONDS = int(os.getenv("CODINGAI_PAUSE_WINDOW_SECONDS", "10") or "10")
MAX_PAUSE_SECONDS = int(os.getenv("CODINGAI_MAX_PAUSE_SECONDS", "300") or "300")

load_dotenv(str(ENV_FILE))


def load_state():
    if not os.path.exists(str(STATE_FILE)):
        return {}
    with open(str(STATE_FILE), "r") as f:
        return json.load(f)


def save_state(state):
    with open(str(STATE_FILE), "w") as f:
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


def _pipeline_state(issue_state: dict) -> dict:
    return issue_state.setdefault("pipeline", {})


def _set_pipeline_stage(state, repo, number, stage: str, **extras):
    issue_state = _get_issue_state(state, repo, number)
    pipeline = _pipeline_state(issue_state)
    pipeline["stage"] = stage
    pipeline["updated_at"] = int(time.time())
    for k, v in extras.items():
        pipeline[k] = v
    save_state(state)
    return pipeline


def _capture_diff(repo_path: str, max_chars: int = 20000):
    try:
        r = subprocess.run(
            ["git", "diff"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        diff = r.stdout or ""
    except Exception as e:
        diff = f"diff capture failed: {e}"
    return diff[:max_chars], len(diff)


def _await_pause_window(repo: str, issue_number: str, *, initial_deadline: float):
    """
    Wait for pause window with optional user pause/cancel controls.
    Respects MAX_PAUSE_SECONDS cap for user pauses.
    """
    start_ts = time.time()
    while True:
        now = time.time()
        state_live = load_state()
        issue_state_live = state_live.get(repo, {}).get(str(issue_number), {})
        pipeline = issue_state_live.get("pipeline", {}) if isinstance(issue_state_live, dict) else {}

        if pipeline.get("cancel_requested"):
            _set_pipeline_stage(
                state_live,
                repo,
                issue_number,
                "canceled",
                canceled_at=int(now),
                cancel_reason=pipeline.get("cancel_reason", "user_cancel"),
            )
            raise RuntimeError("Pipeline canceled by user")

        deadline = float(pipeline.get("pause_deadline", initial_deadline))
        paused = bool(pipeline.get("paused", False))
        paused_at = float(pipeline.get("paused_at", now))

        if paused:
            if now - paused_at > MAX_PAUSE_SECONDS:
                # auto-resume after max pause window
                pipeline["paused"] = False
                pipeline["pause_requested"] = False
                pipeline["pause_deadline"] = int(now + 2)
                pipeline["updated_at"] = int(now)
                save_state(state_live)
            else:
                time.sleep(1.0)
                continue

        if now >= deadline:
            break

        time.sleep(0.5)

    elapsed = time.time() - start_ts
    record_event(
        "pause_window_complete",
        repo=repo,
        issue_number=issue_number,
        status="continued",
        duration_ms=int(elapsed * 1000),
        data={"deadline": int(initial_deadline)},
    )


def _elapsed_ms(started_at):
    if started_at is None:
        return None
    return max(0, int((time.time() - float(started_at)) * 1000))


def _record_issue_outcome(state, repo, number, status, *, dry_run, started_at=None, data=None, persist=True):
    issue_state = _get_issue_state(state, repo, number)
    duration_ms = _elapsed_ms(started_at)
    issue_state["last_status"] = str(status)
    issue_state["dry_run"] = bool(dry_run)
    issue_state["last_processed_at"] = int(time.time())
    if duration_ms is not None:
        issue_state["last_duration_ms"] = duration_ms
    if persist:
        save_state(state)

    labels = {
        "repo": repo,
        "status": str(status),
        "dry_run": "true" if dry_run else "false",
    }
    inc_counter("codingai_issue_runs_total", labels=labels)
    if duration_ms is not None:
        observe_duration_ms(
            "codingai_issue_duration_ms",
            duration_ms,
            labels={"repo": repo, "status": str(status)},
        )
    record_event(
        "issue_outcome",
        repo=repo,
        issue_number=number,
        status=str(status),
        duration_ms=duration_ms,
        data=data or {},
    )
    return duration_ms


def _evaluate_ci_gate(ci_summary, ci_policy):
    allowed = {str(x).strip().lower() for x in ci_policy.get("allowed_check_conclusions", []) if str(x).strip()}
    if not allowed:
        allowed = {"success", "neutral", "skipped"}

    combined_state = str(ci_summary.get("combined_state", "")).lower()
    status_failure = int(ci_summary.get("status_failure", 0))
    status_pending = int(ci_summary.get("status_pending", 0))
    check_queued = int(ci_summary.get("check_queued", 0))
    check_in_progress = int(ci_summary.get("check_in_progress", 0))
    check_total = int(ci_summary.get("check_total", 0))
    status_context_total = int(ci_summary.get("status_context_total", 0))
    conclusions = ci_summary.get("check_conclusions", {})

    pending = combined_state == "pending" or status_pending > 0 or check_queued > 0 or check_in_progress > 0
    failing = combined_state in {"error", "failure"} or status_failure > 0
    for conclusion, count in conclusions.items():
        if int(count) <= 0:
            continue
        if str(conclusion).lower() not in allowed:
            failing = True
            break

    checks_present = (status_context_total + check_total) > 0
    should_block = False
    reason = "ok"

    if bool(ci_policy.get("require_checks_present", False)) and not checks_present:
        should_block = True
        reason = "missing_checks"
    elif bool(ci_policy.get("block_on_failed", True)) and failing:
        should_block = True
        reason = "ci_failed"
    elif bool(ci_policy.get("block_on_pending", True)) and pending:
        should_block = True
        reason = "ci_pending"

    return {
        "should_block": should_block,
        "reason": reason,
        "pending": pending,
        "failing": failing,
        "checks_present": checks_present,
        "allowed_conclusions": sorted(allowed),
    }


def _register_failure(state, repo, number, summary, details, *, policy, started_at=None):
    details = (details or "").strip()
    details = details[:12000]
    dry_run = bool(policy.get("dry_run", False))
    publish_failure_issue = bool(policy.get("safety", {}).get("publish_failure_issue", True))
    ai_stop_phrase = str(policy.get("safety", {}).get("ai_stop_phrase", "AI Stop"))
    cooldown_seconds = int(policy.get("safety", {}).get("failure_cooldown_seconds", 6 * 60 * 60))

    if publish_failure_issue and not dry_run:
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
    issue_state["last_status"] = "dry_run_failed" if dry_run else "failed"
    issue_state["last_error"] = details[:2000]
    issue_state["retry_after"] = int(time.time()) + cooldown_seconds
    issue_state["dry_run"] = dry_run
    issue_state["ai_stop_phrase"] = ai_stop_phrase
    issue_state["policy_refreshed_at"] = int(policy.get("_meta", {}).get("loaded_at", int(time.time())))

    pipeline = issue_state.get("pipeline")
    if isinstance(pipeline, dict):
        pipeline["stage"] = pipeline.get("stage", "failed_no_push")
        pipeline["last_error"] = details[:500]
        pipeline["updated_at"] = int(time.time())

    save_state(state)

    _record_issue_outcome(
        state,
        repo,
        number,
        issue_state["last_status"],
        dry_run=dry_run,
        started_at=started_at,
        data={
            "summary": str(summary)[:280],
            "error_excerpt": details[:1000],
            "publish_failure_issue": publish_failure_issue and not dry_run,
        },
        persist=False,
    )


def _apply_strategy_memory_update(state, repo, test_report):
    memory_update = test_report.get("strategy_memory_update")
    if not isinstance(memory_update, dict):
        return
    state.setdefault("strategy_memory", {})[repo] = memory_update


def _apply_error_memory_update(state, repo, test_report):
    updates = test_report.get("error_memory_update")
    if not isinstance(updates, dict) or not updates:
        return
    repo_map = state.setdefault("strategy_error_memory", {}).setdefault(repo, {})
    repo_map.update(updates)


def automation_enabled(state: dict, repo: str) -> bool:
    auto = state.get("automation", {}).get(repo, {})
    return bool(auto.get("enabled", False))


def _mark_ai_stopped(state, repo, issue_number, pr_number, comment_id, phrase):
    now = int(time.time())
    issue_state = _get_issue_state(state, repo, issue_number)
    issue_state["ai_stopped"] = True
    issue_state["ai_stopped_at"] = now
    issue_state["ai_stop_reason"] = phrase
    issue_state["pr_number"] = pr_number
    issue_state["ai_stop_comment_id"] = comment_id

    state.setdefault("pr_controls", {})[str(pr_number)] = {
        "ai_stopped": True,
        "stopped_at": now,
        "reason": phrase,
    }


def _sync_ai_stop_state(state, repo, issue_number, branch, policy):
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

    phrase = str(policy.get("safety", {}).get("ai_stop_phrase", "AI Stop"))
    stopped, comment = has_ai_stop_comment(repo, pr_number, phrase=phrase)
    if not stopped:
        return False

    _mark_ai_stopped(
        state=state,
        repo=repo,
        issue_number=issue_number,
        pr_number=pr_number,
        comment_id=comment.get("id") if comment else None,
        phrase=phrase,
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


def _has_manual_approval(issue, policy):
    approval = policy.get("approval", {})
    required = bool(approval.get("required", False))
    labels_required = set(approval.get("labels", []))
    approval_token = str(approval.get("token", "")).strip().lower()

    if not required:
        return True

    labels = _extract_issue_labels(issue)
    if labels.intersection(labels_required):
        return True

    text = f"{issue.get('title', '')}\n{issue.get('body', '')}".lower()
    if approval_token and approval_token in text:
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


def _utc_week_key():
    now = datetime.now(timezone.utc)
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def _seconds_until_next_utc_week():
    now = datetime.now(timezone.utc)
    days_until_next_monday = (7 - now.weekday()) % 7
    if days_until_next_monday == 0:
        days_until_next_monday = 7
    next_week = (now + timedelta(days=days_until_next_monday)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return max(60, int((next_week - now).total_seconds()))


def _get_daily_pr_count(state, repo, day_key):
    return int(state.get("daily_pr_counts", {}).get(repo, {}).get(day_key, 0))


def _increment_daily_pr_count(state, repo, day_key):
    state.setdefault("daily_pr_counts", {}).setdefault(repo, {})
    current = int(state["daily_pr_counts"][repo].get(day_key, 0))
    state["daily_pr_counts"][repo][day_key] = current + 1


def _get_weekly_pr_count(state, repo, week_key):
    return int(state.get("weekly_pr_counts", {}).get(repo, {}).get(week_key, 0))


def _increment_weekly_pr_count(state, repo, week_key):
    state.setdefault("weekly_pr_counts", {}).setdefault(repo, {})
    current = int(state["weekly_pr_counts"][repo].get(week_key, 0))
    state["weekly_pr_counts"][repo][week_key] = current + 1


def _render_branch_name(template, issue_number, iteration, repo):
    try:
        rendered = str(template).format(issue=issue_number, iteration=iteration, repo=repo)
    except Exception:
        rendered = f"ai/issue-{issue_number}-iter-{iteration}"
    rendered = rendered.strip().replace(" ", "-")
    if not rendered:
        rendered = f"ai/issue-{issue_number}-iter-{iteration}"
    return rendered


def _resolve_branch_for_issue(state, repo, issue_number, policy):
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
        template = policy.get("branch", {}).get("template", "ai/issue-{issue}-iter-{iteration}")
        active_branch = _render_branch_name(template, int(issue_number), next_iter, repo)
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
    added_ops = []

    for op in extra_ops:
        if len(merged) >= max_ops:
            break
        path = op["path"]
        if path in seen_paths:
            continue
        merged.append(op)
        added_ops.append(op)
        seen_paths.add(path)
        added += 1

    return merged, added, added_ops


def _merge_test_reports(primary_report, fallback_report, *, fallback_label):
    p = primary_report if isinstance(primary_report, dict) else {}
    f = fallback_report if isinstance(fallback_report, dict) else {}

    merged = dict(f)
    merged_attempts = []

    for a in p.get("attempts", []):
        item = dict(a)
        item["stage"] = "base_patch"
        merged_attempts.append(item)

    for a in f.get("attempts", []):
        item = dict(a)
        item["stage"] = fallback_label
        merged_attempts.append(item)

    if merged_attempts:
        merged["attempts"] = merged_attempts
    merged["base_patch_result"] = p.get("result")
    merged["fallback_stage"] = fallback_label
    merged["fallback_result"] = f.get("result")
    merged["fallback_applied"] = True
    return merged


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


def process_issue(repo, issue, state, policy=None):
    policy = policy or get_repo_policy(repo)
    number = str(issue["number"])
    issue_started = time.time()
    issue_state = _get_issue_state(state, repo, number)
    job_id = f"{int(issue_started)}-{issue_state.get('branch_iteration', 0)}"
    pr_policy = policy.get("pr", {})
    patch_policy = policy.get("patch", {})
    strategy_policy = policy.get("strategy", {})
    ci_policy = policy.get("ci", {})
    dry_run = bool(policy.get("dry_run", False))

    issue_state["dry_run"] = dry_run
    issue_state["policy_refreshed_at"] = int(policy.get("_meta", {}).get("loaded_at", int(time.time())))

    if not bool(policy.get("enabled", True)):
        _record_issue_outcome(
            state,
            repo,
            number,
            "policy_disabled",
            dry_run=dry_run,
            started_at=issue_started,
            data={"reason": "policy_disabled"},
        )
        print(f"Issue #{number} skipped because repo policy is disabled.")
        return

    record_event(
        "issue_started",
        repo=repo,
        issue_number=number,
        status="started",
        data={"dry_run": dry_run},
    )
    _set_pipeline_stage(
        state,
        repo,
        number,
        "queued",
        job_id=job_id,
        started_at=int(issue_started),
    )

    # resolve active/open PR branch first (iteration naming strategy)
    try:
        branch, existing_pr = _resolve_branch_for_issue(state, repo, number, policy)

        if _sync_ai_stop_state(state, repo, number, branch, policy):
            _record_issue_outcome(
                state,
                repo,
                number,
                "ai_stopped",
                dry_run=dry_run,
                started_at=issue_started,
                data={"reason": "ai_stop_comment"},
            )
            print(f"Issue #{number} has AI Stop on PR comments. Processing disabled.")
            return
    except Exception as e:
        _register_failure(
            state,
            repo,
            number,
            f"Phase 5 preflight failed for issue #{number}.",
            str(e),
            policy=policy,
            started_at=issue_started,
        )
        return

    # keep state synced if an open PR exists for this branch
    if existing_pr:
        issue_state["pr_created"] = True
        issue_state["pr_number"] = existing_pr["number"]
        issue_state["pr_url"] = existing_pr["url"]

    if not _has_manual_approval(issue, policy):
        issue_state["pending_manual_approval"] = True
        _set_pipeline_stage(state, repo, number, "needs_user_input", reason="manual_approval_required")
        _record_issue_outcome(
            state,
            repo,
            number,
            "pending_manual_approval",
            dry_run=dry_run,
            started_at=issue_started,
            data={"reason": "manual_approval_required"},
        )
        print(f"Issue #{number} pending manual approval. Skipping.")
        return
    issue_state["pending_manual_approval"] = False

    if dry_run:
        issue_updated_at = str(issue.get("updated_at", ""))
        if issue_updated_at and issue_updated_at == str(issue_state.get("dry_run_source_issue_updated_at", "")):
            _record_issue_outcome(
                state,
                repo,
                number,
                "dry_run_unchanged",
                dry_run=dry_run,
                started_at=issue_started,
                data={"reason": "source_issue_unchanged"},
            )
            print(f"Issue #{number} unchanged since previous dry-run execution. Skipping.")
            return

    # Existing PR behavior: either skip completely or auto-update only when issue changed.
    if issue_state.get("pr_created"):
        if not bool(pr_policy.get("auto_update_enabled", True)):
            _record_issue_outcome(
                state,
                repo,
                number,
                "auto_update_disabled",
                dry_run=dry_run,
                started_at=issue_started,
                data={"reason": "policy_auto_update_disabled"},
            )
            print(f"Issue #{number} has existing PR and auto-update is disabled. Skipping.")
            return

        issue_updated_at = str(issue.get("updated_at", ""))
        if issue_updated_at and issue_updated_at == str(issue_state.get("source_issue_updated_at", "")):
            _record_issue_outcome(
                state,
                repo,
                number,
                "source_issue_unchanged",
                dry_run=dry_run,
                started_at=issue_started,
                data={"reason": "source_issue_unchanged"},
            )
            print(f"Issue #{number} unchanged since last run; skipping PR auto-update.")
            return

        if bool(ci_policy.get("require_green_before_update", False)):
            pr_number = issue_state.get("pr_number")
            if not pr_number and existing_pr:
                pr_number = existing_pr.get("number")
            if pr_number:
                ci_started = time.time()
                try:
                    ci_summary = get_pr_ci_status(repo, int(pr_number))
                except Exception as ci_error:
                    on_error = str(ci_policy.get("on_error", "allow")).strip().lower()
                    issue_state["ci_gate"] = {
                        "checked_at": int(time.time()),
                        "error": str(ci_error)[:500],
                        "on_error": on_error,
                    }
                    save_state(state)
                    record_event(
                        "ci_gate_error",
                        repo=repo,
                        issue_number=number,
                        status="error",
                        duration_ms=_elapsed_ms(ci_started),
                        data={"on_error": on_error, "error": str(ci_error)[:200]},
                    )
                    inc_counter("codingai_ci_gate_total", labels={"repo": repo, "result": "error"})
                    if on_error == "block":
                        retry_after = int(time.time()) + int(ci_policy.get("retry_after_seconds", 900))
                        issue_state["retry_after"] = retry_after
                        _record_issue_outcome(
                            state,
                            repo,
                            number,
                            "ci_gate_error_blocked",
                            dry_run=dry_run,
                            started_at=issue_started,
                            data={"error": str(ci_error)[:200], "retry_after": retry_after},
                        )
                        print(f"Issue #{number} blocked by CI gate error policy.")
                        return
                else:
                    decision = _evaluate_ci_gate(ci_summary, ci_policy)
                    issue_state["ci_gate"] = {
                        "checked_at": int(time.time()),
                        "pr_number": int(pr_number),
                        "summary": ci_summary,
                        "decision": decision,
                    }
                    save_state(state)
                    gate_status = "blocked" if decision.get("should_block") else "pass"
                    record_event(
                        "ci_gate_checked",
                        repo=repo,
                        issue_number=number,
                        status=gate_status,
                        duration_ms=_elapsed_ms(ci_started),
                        data={"reason": decision.get("reason"), "pending": decision.get("pending"), "failing": decision.get("failing")},
                    )
                    inc_counter("codingai_ci_gate_total", labels={"repo": repo, "result": gate_status})
                    if decision.get("should_block"):
                        retry_after = int(time.time()) + int(ci_policy.get("retry_after_seconds", 900))
                        issue_state["retry_after"] = retry_after
                        _record_issue_outcome(
                            state,
                            repo,
                            number,
                            "ci_gate_blocked",
                            dry_run=dry_run,
                            started_at=issue_started,
                            data={"reason": decision.get("reason"), "retry_after": retry_after},
                        )
                        print(f"Issue #{number} blocked by CI gate ({decision.get('reason')}).")
                        return

    # Cooldown
    if should_skip_due_to_cooldown(state, repo, number):
        _record_issue_outcome(
            state,
            repo,
            number,
            "cooldown",
            dry_run=dry_run,
            started_at=issue_started,
            data={"reason": "retry_after_active"},
        )
        print(f"Issue #{number} in cooldown. Skipping.")
        return

    # Daily and weekly safety caps for creating NEW PRs
    max_prs_per_day = int(pr_policy.get("max_per_day", 0))
    max_prs_per_week = int(pr_policy.get("max_per_week", 0))
    if not dry_run and not issue_state.get("pr_created") and max_prs_per_day > 0:
        day_key = _utc_day_key()
        daily_count = _get_daily_pr_count(state, repo, day_key)
        if daily_count >= max_prs_per_day:
            retry_after = int(time.time()) + _seconds_until_next_utc_day()
            issue_state["retry_after"] = retry_after
            _record_issue_outcome(
                state,
                repo,
                number,
                "daily_pr_limit_reached",
                dry_run=dry_run,
                started_at=issue_started,
                data={"day_key": day_key, "daily_count": daily_count, "max_per_day": max_prs_per_day},
            )
            print(
                f"Repo {repo} reached daily PR cap ({daily_count}/{max_prs_per_day}) on {day_key}. Skipping."
            )
            return

    if not dry_run and not issue_state.get("pr_created") and max_prs_per_week > 0:
        week_key = _utc_week_key()
        weekly_count = _get_weekly_pr_count(state, repo, week_key)
        if weekly_count >= max_prs_per_week:
            retry_after = int(time.time()) + _seconds_until_next_utc_week()
            issue_state["retry_after"] = retry_after
            _record_issue_outcome(
                state,
                repo,
                number,
                "weekly_pr_limit_reached",
                dry_run=dry_run,
                started_at=issue_started,
                data={"week_key": week_key, "weekly_count": weekly_count, "max_per_week": max_prs_per_week},
            )
            print(
                f"Repo {repo} reached weekly PR cap ({weekly_count}/{max_prs_per_week}) on {week_key}. Skipping."
            )
            return

    print(f"\nProcessing issue #{number} on branch {branch}")
    _set_pipeline_stage(state, repo, number, "analyzing", active_branch=branch)

    try:
        default_branch = get_default_branch(repo)
        branch_sha = get_branch_sha(repo, branch)
        base_sha = branch_sha or get_branch_sha(repo, default_branch)
        repo_strategy_memory = state.get("strategy_memory", {}).get(repo, {})
        repo_error_memory = state.get("strategy_error_memory", {}).get(repo, {})

        if not base_sha:
            raise RuntimeError("Could not resolve base SHA for patch pipeline.")

        repo_path = prepare_job_worktree(repo, job_id=job_id, base_sha=base_sha)

        # Ensure local tests run against the exact commit parent we will use for API commit.
        _checkout_local_branch_at_sha(repo_path, branch, base_sha)

        repo_analysis = analyze_repo(repo_path)
        _set_pipeline_stage(state, repo, number, "patching", active_branch=branch)
        patch_started = time.time()
        patch_result = propose_patch_ops(
            repo_path=repo_path,
            repo_name=repo,
            issue=issue,
            repo_analysis=repo_analysis,
            max_ops=int(patch_policy.get("max_patch_ops", 20)),
        )
        patch_duration_ms = _elapsed_ms(patch_started)
        patch_ops = patch_result.get("patch_ops", [])
        patch_result["test_patch_ops_added"] = 0
        observe_duration_ms("codingai_patch_generation_duration_ms", patch_duration_ms or 0, labels={"repo": repo})
        record_event(
            "patch_generated",
            repo=repo,
            issue_number=number,
            status="ok" if patch_ops else "empty",
            duration_ms=patch_duration_ms,
            data={"ops": len(patch_ops), "confidence": float(patch_result.get("confidence", 0.0) or 0.0)},
        )

        if not patch_ops:
            reason = patch_result.get("reason", "Model returned no patch operations.")
            _register_failure(
                state,
                repo,
                number,
                f"Patch generation failed for issue #{number}.",
                reason,
                policy=policy,
                started_at=issue_started,
            )
            return

        auto_test_patch_enabled = bool(patch_policy.get("auto_generate_test_patches", False))
        test_patch_on_failure_only = bool(patch_policy.get("test_patch_on_failure_only", True))
        try:
            test_patch_min_confidence = float(patch_policy.get("test_patch_min_confidence", 0.55))
        except Exception:
            test_patch_min_confidence = 0.55
        test_patch_min_confidence = max(0.0, min(1.0, test_patch_min_confidence))

        patch_result["test_patch_ops_added"] = 0
        patch_result["test_patch_confidence"] = 0.0
        patch_result["test_patch_reason"] = ""
        patch_result["test_patch_applied"] = False
        patch_result["test_patch_min_confidence"] = test_patch_min_confidence

        _apply_patch_ops_locally(repo_path, patch_ops)
        inc_counter("codingai_patch_ops_total", value=len(patch_ops), labels={"repo": repo})

        if not _local_repo_has_changes(repo_path):
            _register_failure(
                state,
                repo,
                number,
                f"Patch ops produced no effective file changes for issue #{number}.",
                patch_result.get("reason", ""),
                policy=policy,
                started_at=issue_started,
            )
            return

        diff_text, diff_len = _capture_diff(repo_path)
        pause_deadline = time.time() + PAUSE_WINDOW_SECONDS
        _set_pipeline_stage(
            state,
            repo,
            number,
            "pause_window",
            diff=diff_text,
            diff_length=diff_len,
            pause_deadline=int(pause_deadline),
            paused=False,
            pause_requested=False,
            cancel_requested=False,
        )
        try:
            _await_pause_window(repo, number, initial_deadline=pause_deadline)
        except RuntimeError as cancel_err:
            _register_failure(
                state,
                repo,
                number,
                f"Pipeline canceled for issue #{number}.",
                str(cancel_err),
                policy=policy,
                started_at=issue_started,
            )
            return

        _set_pipeline_stage(state, repo, number, "testing")
        print("Patch applied locally. Running tests on patched repository...")
        confidence_threshold = float(strategy_policy.get("switch_confidence_threshold", 0.65))
        strategy_max_attempts = max(1, int(strategy_policy.get("max_attempts", 3)))
        strategy_quarantine_threshold = max(1, int(strategy_policy.get("quarantine_threshold", 3)))
        strategy_quarantine_seconds = max(0, int(strategy_policy.get("quarantine_seconds", 12 * 60 * 60)))
        strategy_memory_half_life = max(0, int(strategy_policy.get("memory_half_life_seconds", 7 * 24 * 60 * 60)))

        tests_started = time.time()
        success, output, test_report = run_tests(
            repo_path,
            repo_name=repo,
            max_attempts=strategy_max_attempts,
            strategy_memory=repo_strategy_memory,
            error_memory=repo_error_memory,
            min_confidence_for_switch=confidence_threshold,
            strategy_quarantine_threshold=strategy_quarantine_threshold,
            strategy_quarantine_seconds=strategy_quarantine_seconds,
            strategy_memory_half_life_seconds=strategy_memory_half_life,
        )
        tests_duration_ms = _elapsed_ms(tests_started)
        attempts_count = len(test_report.get("attempts", [])) if isinstance(test_report, dict) else 0
        observe_duration_ms(
            "codingai_test_execution_duration_ms",
            tests_duration_ms or 0,
            labels={"repo": repo, "result": "passed" if success else "failed"},
        )
        inc_counter("codingai_test_runs_total", labels={"repo": repo, "result": "passed" if success else "failed"})
        inc_counter("codingai_test_attempts_total", value=max(1, attempts_count), labels={"repo": repo})
        record_event(
            "tests_finished",
            repo=repo,
            issue_number=number,
            status="passed" if success else "failed",
            duration_ms=tests_duration_ms,
            data={"attempts": attempts_count},
        )

        if (
            not success
            and auto_test_patch_enabled
            and test_patch_on_failure_only
        ):
            fallback_started = time.time()
            record_event(
                "test_patch_fallback_started",
                repo=repo,
                issue_number=number,
                status="started",
                data={"base_attempts": attempts_count},
            )
            test_patch_started = time.time()
            test_patch_result = propose_test_patch_ops(
                repo_path=repo_path,
                repo_name=repo,
                issue=issue,
                repo_analysis=repo_analysis,
                base_patch_ops=patch_ops,
                max_ops=int(patch_policy.get("max_test_patch_ops", 6)),
            )
            test_patch_duration_ms = _elapsed_ms(test_patch_started)
            observe_duration_ms(
                "codingai_test_patch_generation_duration_ms",
                test_patch_duration_ms or 0,
                labels={"repo": repo},
            )
            test_patch_ops = test_patch_result.get("patch_ops", [])
            test_patch_conf = float(test_patch_result.get("confidence", 0.0) or 0.0)
            test_patch_reason = str(test_patch_result.get("reason", ""))
            patch_result["test_patch_confidence"] = test_patch_conf
            patch_result["test_patch_reason"] = test_patch_reason[:500]

            if test_patch_ops and test_patch_conf >= test_patch_min_confidence:
                patch_ops, added, added_ops = _merge_patch_ops(
                    patch_ops,
                    test_patch_ops,
                    max_ops=int(patch_policy.get("max_total_patch_ops", 30)),
                )
                patch_result["test_patch_ops_added"] = added
                if added > 0:
                    _apply_patch_ops_locally(repo_path, added_ops)
                    patch_result["test_patch_applied"] = True
                    inc_counter("codingai_patch_ops_total", value=added, labels={"repo": repo})
                    print(
                        f"Applied staged test patch ops: {added} "
                        f"(candidate={len(test_patch_ops)} conf={test_patch_conf:.2f})"
                    )

                    fallback_strategy_memory = test_report.get("strategy_memory_update", repo_strategy_memory)
                    tests_fallback_started = time.time()
                    fallback_success, fallback_output, fallback_report = run_tests(
                        repo_path,
                        repo_name=repo,
                        max_attempts=strategy_max_attempts,
                        strategy_memory=fallback_strategy_memory,
                        min_confidence_for_switch=confidence_threshold,
                        strategy_quarantine_threshold=strategy_quarantine_threshold,
                        strategy_quarantine_seconds=strategy_quarantine_seconds,
                        strategy_memory_half_life_seconds=strategy_memory_half_life,
                    )
                    fallback_tests_duration_ms = _elapsed_ms(tests_fallback_started)
                    fallback_attempts = len(fallback_report.get("attempts", [])) if isinstance(fallback_report, dict) else 0
                    observe_duration_ms(
                        "codingai_test_execution_duration_ms",
                        fallback_tests_duration_ms or 0,
                        labels={"repo": repo, "result": "passed" if fallback_success else "failed"},
                    )
                    inc_counter(
                        "codingai_test_runs_total",
                        labels={"repo": repo, "result": "passed" if fallback_success else "failed"},
                    )
                    inc_counter("codingai_test_attempts_total", value=max(1, fallback_attempts), labels={"repo": repo})
                    record_event(
                        "tests_finished",
                        repo=repo,
                        issue_number=number,
                        status="passed" if fallback_success else "failed",
                        duration_ms=fallback_tests_duration_ms,
                        data={"attempts": fallback_attempts, "stage": "test_patch_fallback"},
                    )

                    success = fallback_success
                    output = fallback_output
                    test_report = _merge_test_reports(
                        test_report,
                        fallback_report,
                        fallback_label="test_patch_fallback",
                    )
                    attempts_count = len(test_report.get("attempts", [])) if isinstance(test_report, dict) else attempts_count
                    record_event(
                        "test_patch_fallback_finished",
                        repo=repo,
                        issue_number=number,
                        status="applied",
                        duration_ms=_elapsed_ms(fallback_started),
                        data={"added_ops": added, "confidence": test_patch_conf, "fallback_success": fallback_success},
                    )
                else:
                    record_event(
                        "test_patch_fallback_finished",
                        repo=repo,
                        issue_number=number,
                        status="skipped",
                        duration_ms=_elapsed_ms(fallback_started),
                        data={"reason": "no_unique_test_patch_ops", "confidence": test_patch_conf},
                    )
            else:
                skip_reason = "low_confidence" if test_patch_ops else "no_test_patch_ops"
                record_event(
                    "test_patch_fallback_finished",
                    repo=repo,
                    issue_number=number,
                    status="skipped",
                    duration_ms=_elapsed_ms(fallback_started),
                    data={
                        "reason": skip_reason,
                        "confidence": test_patch_conf,
                        "min_confidence": test_patch_min_confidence,
                        "model_reason": test_patch_reason[:200],
                    },
                )

        _apply_strategy_memory_update(state, repo, test_report)
        _apply_error_memory_update(state, repo, test_report)

        if not success:
            _register_failure(
                state,
                repo,
                number,
                f"Patched repository failed tests for issue #{number}.",
                output,
                policy=policy,
                started_at=issue_started,
            )
            return
        _set_pipeline_stage(
            state,
            repo,
            number,
            "passed",
            test_output_excerpt=str(output or "")[:2000],
            attempts=attempts_count,
        )

        if dry_run:
            issue_state["active_branch"] = branch
            issue_state["patch_ops_count"] = len(patch_ops)
            issue_state["patch_confidence"] = patch_result.get("confidence", 0.0)
            issue_state["test_patch_applied"] = bool(patch_result.get("test_patch_applied", False))
            issue_state["test_patch_ops_added"] = int(patch_result.get("test_patch_ops_added", 0))
            issue_state["test_patch_confidence"] = float(patch_result.get("test_patch_confidence", 0.0))
            issue_state["strategy_confidence_threshold"] = confidence_threshold
            issue_state["strategy_max_attempts"] = strategy_max_attempts
            issue_state["strategy_quarantine_threshold"] = strategy_quarantine_threshold
            issue_state["strategy_quarantine_seconds"] = strategy_quarantine_seconds
            issue_state["dry_run_source_issue_updated_at"] = str(issue.get("updated_at", ""))
            issue_state["retry_after"] = 0
            issue_state["dry_run_actions"] = {
                "would_create_branch": not bool(branch_sha),
                "would_commit_patch_ops": len(patch_ops),
                "would_create_or_update_pr": True,
                "would_publish_check_run": bool(pr_policy.get("enable_github_checks", True)),
                "would_upsert_pr_comment": True,
            }
            _set_pipeline_stage(
                state,
                repo,
                number,
                "failed_no_push" if not success else "passed",
                dry_run=True,
            )
            _record_issue_outcome(
                state,
                repo,
                number,
                "dry_run_passed",
                dry_run=True,
                started_at=issue_started,
                data={"patch_ops_count": len(patch_ops), "attempts": attempts_count},
            )
            print("Dry-run mode enabled. GitHub write operations were skipped.")
            return

        print("Patched tests passed. Creating commit via GitHub API...")
        _set_pipeline_stage(state, repo, number, "pushing")

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
                policy=policy,
                started_at=issue_started,
            )
            return

        if pr_info.get("created"):
            _increment_daily_pr_count(state, repo, _utc_day_key())
            _increment_weekly_pr_count(state, repo, _utc_week_key())

        print("PR URL:", pr_info["url"])

        check_run_url = None
        if bool(pr_policy.get("enable_github_checks", True)):
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
                    name=str(pr_policy.get("check_run_name", "CodingAI Local Validation")),
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
        issue_state["patch_ops_count"] = len(patch_ops)
        issue_state["patch_confidence"] = patch_result.get("confidence", 0.0)
        issue_state["test_patch_applied"] = bool(patch_result.get("test_patch_applied", False))
        issue_state["test_patch_ops_added"] = int(patch_result.get("test_patch_ops_added", 0))
        issue_state["test_patch_confidence"] = float(patch_result.get("test_patch_confidence", 0.0))
        issue_state["strategy_confidence_threshold"] = confidence_threshold
        issue_state["strategy_max_attempts"] = strategy_max_attempts
        issue_state["strategy_quarantine_threshold"] = strategy_quarantine_threshold
        issue_state["strategy_quarantine_seconds"] = strategy_quarantine_seconds
        issue_state["pr_number"] = pr_info["number"]
        issue_state["pr_url"] = pr_info["url"]
        issue_state["check_run_url"] = check_run_url
        issue_state["report_comment_id"] = comment_result.get("id")
        issue_state["source_issue_updated_at"] = str(issue.get("updated_at", ""))
        issue_state["retry_after"] = 0
        issue_state["dry_run"] = False
        _set_pipeline_stage(
            state,
            repo,
            number,
            "pr_created",
            pr_number=pr_info["number"],
            pr_url=pr_info["url"],
            check_run_url=check_run_url,
        )
        _record_issue_outcome(
            state,
            repo,
            number,
            "passed",
            dry_run=False,
            started_at=issue_started,
            data={"patch_ops_count": len(patch_ops), "attempts": attempts_count, "pr_number": pr_info["number"]},
        )
        record_event(
            "issue_github_write_complete",
            repo=repo,
            issue_number=number,
            status="passed",
            data={"pr_number": pr_info["number"], "check_run_url": check_run_url},
        )

    except Exception as e:
        _register_failure(
            state,
            repo,
            number,
            f"Patch pipeline failed for issue #{number}.",
            str(e),
            policy=policy,
            started_at=issue_started,
        )


def _run_repo_cycle(repo):
    cycle_started = time.time()
    cycle_status = "ok"
    issues_count = 0
    record_event("repo_cycle_start", repo=repo, status="started")

    state = load_state()
    repo_policy = get_repo_policy(repo)

    llm_ready = ensure_llm_ready(force=False)
    llm_runtime = get_llm_runtime_status()
    state["llm_runtime"] = {
        "ready": bool(llm_ready.get("ready")),
        "active_provider": llm_ready.get("active_provider"),
        "requested_provider": llm_ready.get("requested_provider"),
        "provider_chain": llm_ready.get("provider_chain", []),
        "checked_at": llm_ready.get("checked_at", int(time.time())),
        "checks": llm_ready.get("checks", []),
        "telemetry_counters": llm_runtime.get("telemetry_counters", {}),
        "telemetry_file": llm_runtime.get("telemetry_file"),
    }
    state.setdefault("policy_runtime", {})[repo] = {
        "enabled": bool(repo_policy.get("enabled", True)),
        "dry_run": bool(repo_policy.get("dry_run", False)),
        "max_prs_per_day": int(repo_policy.get("pr", {}).get("max_per_day", 0)),
        "max_prs_per_week": int(repo_policy.get("pr", {}).get("max_per_week", 0)),
        "policy_loaded_at": int(repo_policy.get("_meta", {}).get("loaded_at", int(time.time()))),
        "policy_errors": list(repo_policy.get("_meta", {}).get("errors", [])),
    }
    save_state(state)

    if not repo_policy.get("enabled", True):
        cycle_status = "policy_disabled"
        print(f"Policy disabled repo cycle for {repo}.")
    elif not llm_ready.get("ready"):
        cycle_status = "llm_not_ready"
        print(
            f"LLM not ready (requested={llm_ready.get('requested_provider')}, "
            f"chain={llm_ready.get('provider_chain')}). Skipping repo cycle for {repo}."
        )
    else:
        try:
            issues = get_ai_issues(repo)
            issues_count = len(issues)
        except Exception as e:
            cycle_status = "issues_fetch_failed"
            print(f"Failed to fetch issues for {repo}: {e}")
        else:
            if not issues:
                cycle_status = "no_issues"
            for issue in issues:
                process_issue(repo, issue, state, policy=repo_policy)

    cycle_duration_ms = _elapsed_ms(cycle_started) or 0
    inc_counter("codingai_repo_cycles_total", labels={"repo": repo, "status": cycle_status})
    observe_duration_ms(
        "codingai_repo_cycle_duration_ms",
        cycle_duration_ms,
        labels={"repo": repo, "status": cycle_status},
    )
    set_gauge("codingai_repo_last_cycle_duration_ms", cycle_duration_ms, labels={"repo": repo})
    set_gauge("codingai_repo_last_cycle_issues_count", issues_count, labels={"repo": repo})
    record_event(
        "repo_cycle_end",
        repo=repo,
        status=cycle_status,
        duration_ms=cycle_duration_ms,
        data={"issues_count": issues_count},
    )
    cleanup_jobs()


def run_repo_cycle_once(repo):
    _run_repo_cycle(repo)


def get_available_repos():
    return list(AVAILABLE_REPOS)


def get_repo_policy_config(repo, force=False):
    return get_repo_policy(repo, force=force)


def get_policies_config(force=False):
    return load_policy_snapshot(force=force)


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


def self_checks():
    """Run lightweight self-checks (disk usage, job cleanup)."""
    disk = disk_usage_report()
    record_event(
        "self_check_disk",
        repo=None,
        status="warn" if disk.get("warn") else "ok",
        data=disk,
    )
    cleanup_jobs()


if __name__ == "__main__":
    selected = choose_repo()
    if selected == "__all__":
        print(f"\nAI Agent active for all repos: {', '.join(AVAILABLE_REPOS)}")
        loop_all(AVAILABLE_REPOS)
    else:
        print(f"\nAI Agent active for {selected}")
        loop(selected)
