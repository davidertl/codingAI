"""
Self-tasks: repeated-failure detector and proactive issue creation.

Scans error memory and strategy memory to detect:
- Repeated failures with the same fingerprint across runs
- Repos with all strategies exhausted (quarantined)

Can optionally open a deduped GitHub issue when thresholds are exceeded.
Rate-limited: at most one issue per fingerprint per cooldown window.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from core.memory_store import (
    get_error_memory,
    get_strategy_memory,
    list_strategy_memories,
)
from core.projects_store import DB_FILE
from core.observability import record_event

_TRUTHY = {"1", "true", "yes", "on"}

SELF_TASK_ENABLED = os.getenv("SELF_TASK_ENABLED", "true").strip().lower() in _TRUTHY
SELF_TASK_ISSUE_ENABLED = os.getenv("SELF_TASK_ISSUE_ENABLED", "false").strip().lower() in _TRUTHY
SELF_TASK_ISSUE_REPO = os.getenv("SELF_TASK_ISSUE_REPO", "").strip()  # repo to file issues against
SELF_TASK_FAILURE_THRESHOLD = int(os.getenv("SELF_TASK_FAILURE_THRESHOLD", "3"))
SELF_TASK_COOLDOWN_SECONDS = int(os.getenv("SELF_TASK_COOLDOWN_SECONDS", str(24 * 3600)))  # 24h

_LOCK = threading.Lock()


# ── SQLite table for self-task tracking ────────────────────────────

def _connect() -> sqlite3.Connection:
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_FILE), timeout=20, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_self_tasks_table() -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS self_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                repo TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                detail_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'open',
                snoozed_until INTEGER NOT NULL DEFAULT 0,
                issue_number INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(kind, fingerprint)
            )
            """
        )
        conn.commit()


# ── CRUD ───────────────────────────────────────────────────────────

def list_self_tasks(*, status: str | None = None, limit: int = 200) -> list[dict]:
    """Return self-task rows, newest first."""
    init_self_tasks_table()
    sql = "SELECT * FROM self_tasks"
    params: list = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(min(limit, 2000))
    with _LOCK, _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_self_task(task_id: int) -> dict | None:
    init_self_tasks_table()
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM self_tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_dict(row) if row else None


def snooze_self_task(task_id: int, seconds: int = 0) -> dict | None:
    """Snooze a self-task for *seconds*.  0 = un-snooze."""
    init_self_tasks_table()
    now = int(time.time())
    snoozed_until = now + max(0, seconds) if seconds > 0 else 0
    with _LOCK, _connect() as conn:
        conn.execute(
            "UPDATE self_tasks SET snoozed_until = ?, status = ?, updated_at = ? WHERE id = ?",
            (snoozed_until, "snoozed" if seconds > 0 else "open", now, task_id),
        )
        conn.commit()
    return get_self_task(task_id)


def dismiss_self_task(task_id: int) -> dict | None:
    """Mark a self-task as dismissed."""
    init_self_tasks_table()
    now = int(time.time())
    with _LOCK, _connect() as conn:
        conn.execute(
            "UPDATE self_tasks SET status = 'dismissed', updated_at = ? WHERE id = ?",
            (now, task_id),
        )
        conn.commit()
    return get_self_task(task_id)


def _upsert_self_task(
    *,
    kind: str,
    fingerprint: str,
    repo: str = "",
    title: str = "",
    detail: dict | None = None,
    issue_number: int | None = None,
) -> dict:
    """Create or update a self-task by (kind, fingerprint)."""
    init_self_tasks_table()
    now = int(time.time())
    detail_blob = json.dumps(detail or {}, ensure_ascii=False, default=str)
    with _LOCK, _connect() as conn:
        existing = conn.execute(
            "SELECT id, status, snoozed_until FROM self_tasks WHERE kind = ? AND fingerprint = ?",
            (kind, fingerprint),
        ).fetchone()

        if existing:
            # Don't re-open dismissed or snoozed (if snooze hasn't expired)
            if existing["status"] == "dismissed":
                return _row_to_dict(
                    conn.execute("SELECT * FROM self_tasks WHERE id = ?", (existing["id"],)).fetchone()
                )
            if existing["status"] == "snoozed" and existing["snoozed_until"] > now:
                return _row_to_dict(
                    conn.execute("SELECT * FROM self_tasks WHERE id = ?", (existing["id"],)).fetchone()
                )
            conn.execute(
                """UPDATE self_tasks SET title = ?, detail_json = ?, status = 'open',
                   repo = ?, issue_number = COALESCE(?, issue_number), updated_at = ?
                   WHERE id = ?""",
                (title, detail_blob, repo, issue_number, now, existing["id"]),
            )
            conn.commit()
            return _row_to_dict(
                conn.execute("SELECT * FROM self_tasks WHERE id = ?", (existing["id"],)).fetchone()
            )

        conn.execute(
            """INSERT INTO self_tasks(kind, fingerprint, repo, title, detail_json, status, created_at, updated_at, issue_number)
               VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?)""",
            (kind, fingerprint, repo, title, detail_blob, now, now, issue_number),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM self_tasks WHERE kind = ? AND fingerprint = ?",
            (kind, fingerprint),
        ).fetchone()
        return _row_to_dict(row)


def _row_to_dict(row: sqlite3.Row | None) -> dict:
    if not row:
        return {}
    d = dict(row)
    try:
        d["detail"] = json.loads(d.pop("detail_json", "{}") or "{}")
    except Exception:
        d["detail"] = {}
    return d


# ── Detectors ──────────────────────────────────────────────────────

def detect_repeated_failures() -> list[dict]:
    """Scan error memory for fingerprints that exceeded the failure threshold.

    Returns a list of detected issues (each a dict with repo, fingerprint, count, etc.).
    """
    if not SELF_TASK_ENABLED:
        return []

    results: list[dict] = []
    for mem_row in list_strategy_memories():
        repo = mem_row.get("repo", "")
        if not repo:
            continue
        errors = get_error_memory(repo)
        for fp, data in errors.items():
            fail_count = 0
            if isinstance(data, dict):
                fail_count = int(data.get("fail_count", data.get("count", 0)))
            elif isinstance(data, (int, float)):
                fail_count = int(data)
            if fail_count >= SELF_TASK_FAILURE_THRESHOLD:
                results.append({
                    "repo": repo,
                    "fingerprint": fp,
                    "fail_count": fail_count,
                    "data": data if isinstance(data, dict) else {"count": fail_count},
                })
    return results


def detect_quarantined_repos() -> list[dict]:
    """Find repos where strategy memory indicates quarantine."""
    if not SELF_TASK_ENABLED:
        return []

    results: list[dict] = []
    for mem_row in list_strategy_memories():
        repo = mem_row.get("repo", "")
        memory = mem_row.get("memory", {})
        if not isinstance(memory, dict):
            continue
        quarantined_at = memory.get("quarantined_at") or memory.get("quarantine_until")
        if quarantined_at:
            results.append({
                "repo": repo,
                "quarantined_at": quarantined_at,
                "memory": memory,
            })
    return results


# ── Scan + Upsert ──────────────────────────────────────────────────

def run_self_task_scan() -> dict:
    """Run all detectors, upsert self-tasks, optionally create GitHub issues.

    Returns summary of what was found.
    """
    if not SELF_TASK_ENABLED:
        return {"enabled": False}

    created_tasks: list[dict] = []

    # -- Repeated failures --
    for item in detect_repeated_failures():
        fp = item["fingerprint"]
        repo = item["repo"]
        title = f"Repeated failure: {fp[:80]} in {repo}"
        task = _upsert_self_task(
            kind="repeated_failure",
            fingerprint=f"{repo}:{fp}",
            repo=repo,
            title=title,
            detail=item,
        )
        if task.get("status") == "open":
            created_tasks.append(task)
            _maybe_create_issue(task, repo=repo)

    # -- Quarantined repos --
    for item in detect_quarantined_repos():
        repo = item["repo"]
        title = f"All strategies quarantined for {repo}"
        task = _upsert_self_task(
            kind="quarantined_repo",
            fingerprint=f"quarantine:{repo}",
            repo=repo,
            title=title,
            detail=item,
        )
        if task.get("status") == "open":
            created_tasks.append(task)
            _maybe_create_issue(task, repo=repo)

    summary = {
        "enabled": True,
        "tasks_upserted": len(created_tasks),
        "scanned_at": int(time.time()),
    }

    record_event("self_task_scan", status="ok", data=summary)
    return summary


# ── Optional GitHub issue creation ─────────────────────────────────

_LAST_ISSUE_CREATED: dict[str, float] = {}  # fingerprint → timestamp


def _maybe_create_issue(task: dict, repo: str) -> None:
    """Create a GitHub issue if enabled and cooldown has elapsed."""
    if not SELF_TASK_ISSUE_ENABLED:
        return

    target_repo = SELF_TASK_ISSUE_REPO or repo
    if not target_repo:
        return

    fingerprint = task.get("fingerprint", "")
    now = time.time()
    last = _LAST_ISSUE_CREATED.get(fingerprint, 0)
    if now - last < SELF_TASK_COOLDOWN_SECONDS:
        return  # rate-limited

    try:
        from github.issue_manager import create_issue

        title = f"[CodingAI Self-Task] {task.get('title', fingerprint)}"[:256]
        body_lines = [
            f"**Kind:** {task.get('kind', 'unknown')}",
            f"**Repo:** {repo}",
            f"**Fingerprint:** `{fingerprint}`",
            "",
            "```json",
            json.dumps(task.get("detail", {}), indent=2, ensure_ascii=False, default=str)[:3000],
            "```",
            "",
            "_Auto-created by CodingAI self-task scanner._",
        ]
        result = create_issue(target_repo, title, "\n".join(body_lines))
        issue_number = result.get("number")
        _LAST_ISSUE_CREATED[fingerprint] = now

        # Update the self-task with the issue number
        if issue_number and task.get("id"):
            init_self_tasks_table()
            with _LOCK, _connect() as conn:
                conn.execute(
                    "UPDATE self_tasks SET issue_number = ?, updated_at = ? WHERE id = ?",
                    (issue_number, int(now), task["id"]),
                )
                conn.commit()

        record_event(
            "self_task_issue_created",
            repo=target_repo,
            data={"fingerprint": fingerprint, "issue_number": issue_number},
        )
    except Exception as exc:
        record_event(
            "self_task_issue_failed",
            repo=target_repo,
            status="error",
            data={"fingerprint": fingerprint, "error": str(exc)[:200]},
        )
