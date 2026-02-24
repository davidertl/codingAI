"""
SQLite-backed strategy memory store.

Replaces the ``state["strategy_memory"]`` dict that lived in the flat
``state.json`` with a proper table in ``state.db``.

Schema
------
strategy_memory(repo TEXT PK, memory_json TEXT, updated_at INTEGER)
error_memory(repo TEXT, error_key TEXT, error_json TEXT, updated_at INTEGER, PK(repo,error_key))
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from core.projects_store import DB_FILE  # reuse the same DB

_LOCK = threading.Lock()


def _connect() -> sqlite3.Connection:
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_FILE), timeout=20, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_memory_tables() -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_memory (
                repo TEXT PRIMARY KEY,
                memory_json TEXT NOT NULL DEFAULT '{}',
                updated_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS error_memory (
                repo TEXT NOT NULL,
                error_key TEXT NOT NULL,
                error_json TEXT NOT NULL DEFAULT '{}',
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (repo, error_key)
            )
            """
        )
        conn.commit()


# ── Strategy memory ────────────────────────────────────────────────

def get_strategy_memory(repo: str) -> dict:
    """Return strategy memory dict for *repo*, or empty dict."""
    init_memory_tables()
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT memory_json FROM strategy_memory WHERE repo = ?", (repo,)
        ).fetchone()
    if not row:
        return {}
    try:
        data = json.loads(row["memory_json"] or "{}")
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def set_strategy_memory(repo: str, memory: dict) -> None:
    """Upsert strategy memory for *repo*."""
    if not isinstance(memory, dict):
        return
    init_memory_tables()
    now = int(time.time())
    blob = json.dumps(memory, ensure_ascii=False, default=str)
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            INSERT INTO strategy_memory(repo, memory_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(repo) DO UPDATE SET
              memory_json = excluded.memory_json,
              updated_at = excluded.updated_at
            """,
            (repo, blob, now),
        )
        conn.commit()


def list_strategy_memories() -> list[dict]:
    """Return all strategy memory rows (for UI / diagnostics)."""
    init_memory_tables()
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT repo, memory_json, updated_at FROM strategy_memory ORDER BY updated_at DESC"
        ).fetchall()
    out = []
    for row in rows:
        try:
            data = json.loads(row["memory_json"] or "{}")
        except Exception:
            data = {}
        out.append({
            "repo": row["repo"],
            "memory": data,
            "updated_at": row["updated_at"],
        })
    return out


# ── Error memory ───────────────────────────────────────────────────

def get_error_memory(repo: str) -> dict[str, Any]:
    """Return all error_key→data for *repo*."""
    init_memory_tables()
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT error_key, error_json FROM error_memory WHERE repo = ?", (repo,)
        ).fetchall()
    out: dict[str, Any] = {}
    for row in rows:
        try:
            out[row["error_key"]] = json.loads(row["error_json"] or "{}")
        except Exception:
            out[row["error_key"]] = {}
    return out


def update_error_memory(repo: str, updates: dict) -> None:
    """Merge *updates* into error memory for *repo*."""
    if not isinstance(updates, dict) or not updates:
        return
    init_memory_tables()
    now = int(time.time())
    with _LOCK, _connect() as conn:
        for key, value in updates.items():
            blob = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
            conn.execute(
                """
                INSERT INTO error_memory(repo, error_key, error_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(repo, error_key) DO UPDATE SET
                  error_json = excluded.error_json,
                  updated_at = excluded.updated_at
                """,
                (repo, str(key), blob, now),
            )
        conn.commit()


# ── Migration from state.json ──────────────────────────────────────

def migrate_from_state(state: dict) -> int:
    """Migrate ``state["strategy_memory"]`` and ``state["strategy_error_memory"]``
    into SQLite tables.  Returns count of repos migrated.
    """
    count = 0
    sm = state.get("strategy_memory")
    if isinstance(sm, dict):
        for repo, mem in sm.items():
            if isinstance(mem, dict) and mem:
                set_strategy_memory(str(repo), mem)
                count += 1

    em = state.get("strategy_error_memory")
    if isinstance(em, dict):
        for repo, errs in em.items():
            if isinstance(errs, dict) and errs:
                update_error_memory(str(repo), errs)
                count += 1
    return count
