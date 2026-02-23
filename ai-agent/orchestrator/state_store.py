import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from orchestrator.utils import utc_now_iso
from paths import AI_AGENT_DIR

_DB_RAW = os.getenv("CODINGAI_DB_FILE", "").strip()
DB_FILE = Path(_DB_RAW).expanduser().resolve() if _DB_RAW else (AI_AGENT_DIR / "state.db").resolve()
_LOCK = threading.Lock()


def _connect():
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_FILE), timeout=20, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _now_ts() -> int:
    return int(time.time())


def init_db():
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orchestrator_runs (
              run_id TEXT PRIMARY KEY,
              repo TEXT NOT NULL,
              issue_number INTEGER,
              mode TEXT NOT NULL DEFAULT 'v2',
              status TEXT NOT NULL DEFAULT 'running',
              summary TEXT NOT NULL DEFAULT '',
              error_reason TEXT NOT NULL DEFAULT '',
              success_json TEXT NOT NULL DEFAULT '{}',
              artifacts_json TEXT NOT NULL DEFAULT '{}',
              attempts_count INTEGER NOT NULL DEFAULT 0,
              started_at INTEGER NOT NULL,
              ended_at INTEGER NOT NULL DEFAULT 0,
              started_at_utc TEXT NOT NULL,
              ended_at_utc TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orchestrator_attempts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id TEXT NOT NULL REFERENCES orchestrator_runs(run_id) ON DELETE CASCADE,
              phase TEXT NOT NULL,
              attempt_index INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT '',
              fingerprint TEXT NOT NULL DEFAULT '',
              payload_json TEXT NOT NULL DEFAULT '{}',
              created_at INTEGER NOT NULL,
              created_at_utc TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orchestrator_artifacts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id TEXT NOT NULL REFERENCES orchestrator_runs(run_id) ON DELETE CASCADE,
              name TEXT NOT NULL,
              body TEXT NOT NULL DEFAULT '',
              sha256 TEXT NOT NULL DEFAULT '',
              created_at INTEGER NOT NULL,
              created_at_utc TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orch_runs_repo_started ON orchestrator_runs(repo, started_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orch_attempts_run ON orchestrator_attempts(run_id, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orch_artifacts_run ON orchestrator_artifacts(run_id, id)")
        conn.commit()


def create_run(*, run_id: str, repo: str, issue_number: int | None, mode: str = "v2"):
    init_db()
    now = _now_ts()
    now_utc = utc_now_iso()
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO orchestrator_runs(
              run_id, repo, issue_number, mode, status, summary, error_reason,
              success_json, artifacts_json, attempts_count, started_at, ended_at,
              started_at_utc, ended_at_utc
            ) VALUES (?, ?, ?, ?, 'running', '', '', '{}', '{}', 0, ?, 0, ?, '')
            """,
            (str(run_id), str(repo), issue_number, str(mode), now, now_utc),
        )
        conn.commit()


def append_attempt(
    *,
    run_id: str,
    phase: str,
    attempt_index: int,
    status: str,
    payload: dict | None = None,
    fingerprint: str = "",
):
    init_db()
    now = _now_ts()
    now_utc = utc_now_iso()
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            INSERT INTO orchestrator_attempts(run_id, phase, attempt_index, status, fingerprint, payload_json, created_at, created_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(run_id),
                str(phase),
                int(attempt_index),
                str(status),
                str(fingerprint or ""),
                payload_json,
                now,
                now_utc,
            ),
        )
        conn.execute(
            """
            UPDATE orchestrator_runs
            SET attempts_count = COALESCE(attempts_count, 0) + 1
            WHERE run_id = ?
            """,
            (str(run_id),),
        )
        conn.commit()


def add_artifact(*, run_id: str, name: str, body: str, sha256: str):
    init_db()
    now = _now_ts()
    now_utc = utc_now_iso()
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            INSERT INTO orchestrator_artifacts(run_id, name, body, sha256, created_at, created_at_utc)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (str(run_id), str(name), str(body or ""), str(sha256 or ""), now, now_utc),
        )
        conn.commit()


def finalize_run(
    *,
    run_id: str,
    status: str,
    summary: str,
    error_reason: str = "",
    success_criteria: dict | None = None,
    artifacts: dict | None = None,
):
    init_db()
    now = _now_ts()
    now_utc = utc_now_iso()
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            UPDATE orchestrator_runs
            SET status = ?, summary = ?, error_reason = ?, success_json = ?, artifacts_json = ?, ended_at = ?, ended_at_utc = ?
            WHERE run_id = ?
            """,
            (
                str(status),
                str(summary or ""),
                str(error_reason or ""),
                json.dumps(success_criteria or {}, ensure_ascii=False),
                json.dumps(artifacts or {}, ensure_ascii=False),
                now,
                now_utc,
                str(run_id),
            ),
        )
        conn.commit()


def _row_to_run(row) -> dict:
    if not row:
        return {}
    return {
        "run_id": str(row["run_id"]),
        "repo": str(row["repo"]),
        "issue_number": row["issue_number"],
        "mode": str(row["mode"]),
        "status": str(row["status"]),
        "summary": str(row["summary"] or ""),
        "error_reason": str(row["error_reason"] or ""),
        "success_criteria": _json_load(str(row["success_json"] or "{}"), default={}),
        "artifacts": _json_load(str(row["artifacts_json"] or "{}"), default={}),
        "attempts_count": int(row["attempts_count"] or 0),
        "started_at": int(row["started_at"] or 0),
        "ended_at": int(row["ended_at"] or 0),
        "started_at_utc": str(row["started_at_utc"] or ""),
        "ended_at_utc": str(row["ended_at_utc"] or ""),
    }


def _row_to_attempt(row) -> dict:
    return {
        "id": int(row["id"]),
        "run_id": str(row["run_id"]),
        "phase": str(row["phase"]),
        "attempt_index": int(row["attempt_index"] or 0),
        "status": str(row["status"] or ""),
        "fingerprint": str(row["fingerprint"] or ""),
        "payload": _json_load(str(row["payload_json"] or "{}"), default={}),
        "created_at": int(row["created_at"] or 0),
        "created_at_utc": str(row["created_at_utc"] or ""),
    }


def _row_to_artifact(row) -> dict:
    return {
        "id": int(row["id"]),
        "run_id": str(row["run_id"]),
        "name": str(row["name"]),
        "body": str(row["body"] or ""),
        "sha256": str(row["sha256"] or ""),
        "created_at": int(row["created_at"] or 0),
        "created_at_utc": str(row["created_at_utc"] or ""),
    }


def _json_load(raw: str, *, default):
    try:
        value = json.loads(raw)
    except Exception:
        return default
    if isinstance(default, dict) and isinstance(value, dict):
        return value
    if isinstance(default, list) and isinstance(value, list):
        return value
    return default


def get_run(run_id: str) -> dict | None:
    init_db()
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM orchestrator_runs WHERE run_id = ?", (str(run_id),)).fetchone()
    if not row:
        return None
    return _row_to_run(row)


def list_runs(*, repo: str | None = None, limit: int = 50) -> list[dict]:
    init_db()
    query = "SELECT * FROM orchestrator_runs"
    params = []
    if repo:
        query += " WHERE repo = ?"
        params.append(str(repo))
    query += " ORDER BY started_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), 500)))
    with _LOCK, _connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_row_to_run(r) for r in rows]


def list_attempts(run_id: str, *, limit: int = 500) -> list[dict]:
    init_db()
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM orchestrator_attempts WHERE run_id = ? ORDER BY id ASC LIMIT ?",
            (str(run_id), max(1, min(int(limit), 2000))),
        ).fetchall()
    return [_row_to_attempt(r) for r in rows]


def list_artifacts(run_id: str, *, limit: int = 300) -> list[dict]:
    init_db()
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM orchestrator_artifacts WHERE run_id = ? ORDER BY id ASC LIMIT ?",
            (str(run_id), max(1, min(int(limit), 2000))),
        ).fetchall()
    return [_row_to_artifact(r) for r in rows]
