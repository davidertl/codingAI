import hashlib
import hmac
import json
import logging
import os
import sqlite3
import time
from pathlib import Path

from core.observability import record_event

log = logging.getLogger(__name__)

GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "").strip()
WEBHOOK_ENABLED = os.getenv("GITHUB_WEBHOOK_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
AI_STOP_PHRASE = os.getenv("AI_STOP_PHRASE", "AI Stop").strip()
AI_START_PHRASE = os.getenv("AI_START_PHRASE", "AI Start").strip()
AI_FIX_LABEL = os.getenv("AI_FIX_LABEL", "ai-fix").strip().lower()

_DB_PATH = os.getenv("WEBHOOK_DB_PATH", "")
_delivery_db: sqlite3.Connection | None = None


def _get_db() -> sqlite3.Connection:
    global _delivery_db
    if _delivery_db is not None:
        return _delivery_db
    db_path = _DB_PATH or str(Path(__file__).resolve().parent.parent / "state.db")
    _delivery_db = sqlite3.connect(db_path, check_same_thread=False)
    _delivery_db.execute("PRAGMA journal_mode=WAL")
    _delivery_db.execute(
        "CREATE TABLE IF NOT EXISTS webhook_deliveries ("
        "  delivery_id TEXT PRIMARY KEY,"
        "  event_type TEXT,"
        "  received_at REAL"
        ")"
    )
    _delivery_db.commit()
    return _delivery_db


def verify_webhook_signature(payload: bytes, signature: str, secret: str | None = None) -> bool:
    secret = secret or GITHUB_WEBHOOK_SECRET
    if not secret:
        return not signature
    expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _is_duplicate(delivery_id: str) -> bool:
    if not delivery_id:
        return False
    db = _get_db()
    row = db.execute(
        "SELECT 1 FROM webhook_deliveries WHERE delivery_id = ?", (delivery_id,)
    ).fetchone()
    if row:
        return True
    db.execute(
        "INSERT INTO webhook_deliveries (delivery_id, event_type, received_at) VALUES (?, ?, ?)",
        (delivery_id, "", time.time()),
    )
    db.commit()
    return False


_webhook_queue: list[dict] = []
_last_webhook_at: float = 0.0


def get_last_webhook_at() -> float:
    return _last_webhook_at


def get_queued_events() -> list[dict]:
    events = _webhook_queue[:]
    del _webhook_queue[:]
    return events


async def handle_webhook(event_type: str, payload: dict, delivery_id: str = ""):
    global _last_webhook_at
    _last_webhook_at = time.time()

    if _is_duplicate(delivery_id):
        record_event("webhook_duplicate", status="skipped", data={"delivery_id": delivery_id})
        return

    record_event(
        "webhook_received",
        status="ok",
        data={"event_type": event_type, "delivery_id": delivery_id},
    )

    if event_type == "issues":
        await handle_issue_event(payload)
    elif event_type == "issue_comment":
        await handle_comment_event(payload)
    elif event_type == "pull_request":
        await handle_pr_event(payload)


async def handle_issue_event(payload: dict):
    action = str(payload.get("action") or "").strip()
    issue = payload.get("issue") or {}
    repo_full = str((payload.get("repository") or {}).get("full_name") or "").strip()
    number = int(issue.get("number") or 0)
    labels = [
        str(lbl.get("name") or "").strip().lower()
        for lbl in (issue.get("labels") or [])
        if isinstance(lbl, dict)
    ]

    if action in {"opened", "labeled"} and AI_FIX_LABEL in labels:
        _webhook_queue.append({
            "type": "issue_ready",
            "repo": repo_full,
            "issue_number": number,
            "action": action,
            "source": "webhook",
            "received_at": time.time(),
        })
        record_event(
            "webhook_issue_queued",
            repo=repo_full,
            issue_number=number,
            status="ok",
            data={"action": action},
        )

    elif action == "closed":
        _webhook_queue.append({
            "type": "issue_closed",
            "repo": repo_full,
            "issue_number": number,
            "source": "webhook",
            "received_at": time.time(),
        })


async def handle_comment_event(payload: dict):
    action = str(payload.get("action") or "").strip()
    if action != "created":
        return

    comment = payload.get("comment") or {}
    body = str(comment.get("body") or "").strip()
    issue = payload.get("issue") or {}
    repo_full = str((payload.get("repository") or {}).get("full_name") or "").strip()
    number = int(issue.get("number") or 0)

    body_lower = body.lower()
    stop_phrase_lower = AI_STOP_PHRASE.lower()
    start_phrase_lower = AI_START_PHRASE.lower()

    if stop_phrase_lower in body_lower:
        _webhook_queue.append({
            "type": "ai_stop",
            "repo": repo_full,
            "issue_number": number,
            "comment_id": comment.get("id"),
            "source": "webhook",
            "received_at": time.time(),
        })
        record_event(
            "webhook_ai_stop",
            repo=repo_full,
            issue_number=number,
            status="ok",
        )

    elif start_phrase_lower in body_lower:
        _webhook_queue.append({
            "type": "ai_start",
            "repo": repo_full,
            "issue_number": number,
            "comment_id": comment.get("id"),
            "source": "webhook",
            "received_at": time.time(),
        })
        record_event(
            "webhook_ai_start",
            repo=repo_full,
            issue_number=number,
            status="ok",
        )


async def handle_pr_event(payload: dict):
    action = str(payload.get("action") or "").strip()
    pr = payload.get("pull_request") or {}
    repo_full = str((payload.get("repository") or {}).get("full_name") or "").strip()
    number = int(pr.get("number") or 0)

    if action in {"closed", "merged"}:
        _webhook_queue.append({
            "type": "pr_closed",
            "repo": repo_full,
            "pr_number": number,
            "merged": bool(pr.get("merged")),
            "source": "webhook",
            "received_at": time.time(),
        })


def cleanup_old_deliveries(max_age_seconds: int = 7 * 24 * 3600):
    try:
        db = _get_db()
        cutoff = time.time() - max_age_seconds
        db.execute("DELETE FROM webhook_deliveries WHERE received_at < ?", (cutoff,))
        db.commit()
    except Exception:
        pass
