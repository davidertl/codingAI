import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from paths import AI_AGENT_DIR

_DB_RAW = os.getenv("CODINGAI_DB_FILE", "").strip()
DB_FILE = Path(_DB_RAW).expanduser().resolve() if _DB_RAW else (AI_AGENT_DIR / "state.db").resolve()
_LOCK = threading.Lock()
_VALID_TASK_TYPES = {"review", "planning", "research", "chat"}
_VALID_ROLES = {"system", "user", "assistant", "tool"}


def db_path() -> str:
    return str(DB_FILE)


def _now() -> int:
    return int(time.time())


def _connect():
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_FILE), timeout=20, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _normalize_task_type(task_type: str | None, *, default: str = "chat") -> str:
    value = str(task_type or "").strip().lower()
    if value in _VALID_TASK_TYPES:
        return value
    return default


def _normalize_role(role: str | None) -> str:
    value = str(role or "").strip().lower()
    if value in _VALID_ROLES:
        return value
    raise ValueError(f"Invalid role: {role}")


def _json_loads_list(value: str | None) -> list:
    if not value:
        return []
    try:
        data = json.loads(value)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _json_loads_dict(value: str | None) -> dict:
    if not value:
        return {}
    try:
        data = json.loads(value)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def init_db():
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS threads (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              repo TEXT NOT NULL,
              title TEXT NOT NULL DEFAULT '',
              task_type TEXT NOT NULL DEFAULT 'chat',
              model TEXT NOT NULL DEFAULT '',
              archived INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              thread_id INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
              role TEXT NOT NULL,
              content TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'completed',
              task_type TEXT NOT NULL DEFAULT 'chat',
              model TEXT NOT NULL DEFAULT '',
              meta_json TEXT NOT NULL DEFAULT '{}',
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS attachments (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              thread_id INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
              message_id INTEGER REFERENCES messages(id) ON DELETE CASCADE,
              repo TEXT NOT NULL DEFAULT '',
              kind TEXT NOT NULL DEFAULT 'snippet',
              path TEXT NOT NULL DEFAULT '',
              start_line INTEGER NOT NULL DEFAULT 1,
              end_line INTEGER NOT NULL DEFAULT 1,
              snippet TEXT NOT NULL DEFAULT '',
              meta_json TEXT NOT NULL DEFAULT '{}',
              created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_threads_repo_updated ON threads(repo, updated_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_thread_created ON messages(thread_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_attachments_thread_created ON attachments(thread_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_attachments_message_created ON attachments(message_id, created_at)")
        conn.commit()


def _thread_from_row(row) -> dict:
    return {
        "id": int(row["id"]),
        "repo": str(row["repo"] or ""),
        "title": str(row["title"] or ""),
        "task_type": _normalize_task_type(row["task_type"]),
        "model": str(row["model"] or ""),
        "archived": bool(int(row["archived"])),
        "created_at": int(row["created_at"] or 0),
        "updated_at": int(row["updated_at"] or 0),
    }


def _message_from_row(row) -> dict:
    return {
        "id": int(row["id"]),
        "thread_id": int(row["thread_id"]),
        "role": str(row["role"] or ""),
        "content": str(row["content"] or ""),
        "status": str(row["status"] or ""),
        "task_type": _normalize_task_type(row["task_type"]),
        "model": str(row["model"] or ""),
        "meta": _json_loads_dict(row["meta_json"]),
        "created_at": int(row["created_at"] or 0),
        "updated_at": int(row["updated_at"] or 0),
    }


def _attachment_from_row(row) -> dict:
    return {
        "id": int(row["id"]),
        "thread_id": int(row["thread_id"]),
        "message_id": int(row["message_id"]) if row["message_id"] is not None else None,
        "repo": str(row["repo"] or ""),
        "kind": str(row["kind"] or "snippet"),
        "path": str(row["path"] or ""),
        "start_line": int(row["start_line"] or 1),
        "end_line": int(row["end_line"] or 1),
        "snippet": str(row["snippet"] or ""),
        "meta": _json_loads_dict(row["meta_json"]),
        "created_at": int(row["created_at"] or 0),
    }


def create_thread(*, repo: str, title: str = "", task_type: str = "chat", model: str = "") -> dict:
    init_db()
    now = _now()
    with _LOCK, _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO threads(repo, title, task_type, model, archived, created_at, updated_at)
            VALUES (?, ?, ?, ?, 0, ?, ?)
            """,
            (
                str(repo or "").strip(),
                str(title or "").strip(),
                _normalize_task_type(task_type),
                str(model or "").strip(),
                now,
                now,
            ),
        )
        thread_id = int(cur.lastrowid)
        row = conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
        conn.commit()
    return _thread_from_row(row)


def get_thread(thread_id: int) -> dict | None:
    init_db()
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM threads WHERE id = ?", (int(thread_id),)).fetchone()
    if not row:
        return None
    return _thread_from_row(row)


def list_threads(repo: str | None = None, *, include_archived: bool = False, limit: int = 200) -> list[dict]:
    init_db()
    query = "SELECT * FROM threads"
    params = []
    where = []
    if repo is not None:
        where.append("repo = ?")
        params.append(str(repo))
    if not include_archived:
        where.append("archived = 0")
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY updated_at DESC, id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 1000)))

    with _LOCK, _connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_thread_from_row(r) for r in rows]


def update_thread(thread_id: int, *, title: str | None = None, archived: bool | None = None, task_type: str | None = None):
    init_db()
    existing = get_thread(thread_id)
    if not existing:
        return None

    next_title = existing["title"] if title is None else str(title or "").strip()
    next_archived = existing["archived"] if archived is None else bool(archived)
    next_task_type = existing["task_type"] if task_type is None else _normalize_task_type(task_type)
    now = _now()
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            UPDATE threads
            SET title = ?, archived = ?, task_type = ?, updated_at = ?
            WHERE id = ?
            """,
            (next_title, 1 if next_archived else 0, next_task_type, now, int(thread_id)),
        )
        row = conn.execute("SELECT * FROM threads WHERE id = ?", (int(thread_id),)).fetchone()
        conn.commit()
    return _thread_from_row(row)


def delete_thread(thread_id: int) -> bool:
    init_db()
    with _LOCK, _connect() as conn:
        cur = conn.execute("DELETE FROM threads WHERE id = ?", (int(thread_id),))
        conn.commit()
    return int(cur.rowcount or 0) > 0


def create_message(
    *,
    thread_id: int,
    role: str,
    content: str,
    status: str = "completed",
    task_type: str = "chat",
    model: str = "",
    meta: dict | None = None,
) -> dict:
    init_db()
    now = _now()
    normalized_role = _normalize_role(role)
    normalized_task_type = _normalize_task_type(task_type)
    with _LOCK, _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO messages(thread_id, role, content, status, task_type, model, meta_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(thread_id),
                normalized_role,
                str(content or ""),
                str(status or "completed"),
                normalized_task_type,
                str(model or ""),
                json.dumps(meta or {}, ensure_ascii=True),
                now,
                now,
            ),
        )
        message_id = int(cur.lastrowid)
        conn.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (now, int(thread_id)))
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        conn.commit()
    return _message_from_row(row)


def get_message(message_id: int) -> dict | None:
    init_db()
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (int(message_id),)).fetchone()
    if not row:
        return None
    return _message_from_row(row)


def update_message(
    message_id: int,
    *,
    content: str | None = None,
    status: str | None = None,
    model: str | None = None,
    meta: dict | None = None,
):
    init_db()
    existing = get_message(message_id)
    if not existing:
        return None
    now = _now()
    next_content = existing["content"] if content is None else str(content)
    next_status = existing["status"] if status is None else str(status)
    next_model = existing["model"] if model is None else str(model)
    next_meta = existing["meta"] if meta is None else dict(meta)
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            UPDATE messages
            SET content = ?, status = ?, model = ?, meta_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_content,
                next_status,
                next_model,
                json.dumps(next_meta, ensure_ascii=True),
                now,
                int(message_id),
            ),
        )
        conn.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (now, int(existing["thread_id"])))
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (int(message_id),)).fetchone()
        conn.commit()
    return _message_from_row(row)


def list_messages(thread_id: int, *, limit: int = 500) -> list[dict]:
    init_db()
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE thread_id = ? ORDER BY id ASC LIMIT ?",
            (int(thread_id), max(1, min(int(limit), 2000))),
        ).fetchall()
    return [_message_from_row(r) for r in rows]


def create_attachment(
    *,
    thread_id: int,
    message_id: int | None,
    repo: str,
    kind: str,
    path: str,
    start_line: int,
    end_line: int,
    snippet: str,
    meta: dict | None = None,
) -> dict:
    init_db()
    now = _now()
    with _LOCK, _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO attachments(thread_id, message_id, repo, kind, path, start_line, end_line, snippet, meta_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(thread_id),
                int(message_id) if message_id is not None else None,
                str(repo or ""),
                str(kind or "snippet"),
                str(path or ""),
                max(1, int(start_line or 1)),
                max(1, int(end_line or 1)),
                str(snippet or ""),
                json.dumps(meta or {}, ensure_ascii=True),
                now,
            ),
        )
        attachment_id = int(cur.lastrowid)
        row = conn.execute("SELECT * FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
        conn.commit()
    return _attachment_from_row(row)


def list_attachments(
    *,
    thread_id: int | None = None,
    message_id: int | None = None,
    limit: int = 500,
) -> list[dict]:
    init_db()
    query = "SELECT * FROM attachments"
    where = []
    params = []
    if thread_id is not None:
        where.append("thread_id = ?")
        params.append(int(thread_id))
    if message_id is not None:
        where.append("message_id = ?")
        params.append(int(message_id))
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY id ASC LIMIT ?"
    params.append(max(1, min(int(limit), 2000)))

    with _LOCK, _connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_attachment_from_row(r) for r in rows]


def get_attachments_map_for_thread(thread_id: int) -> dict[int, list[dict]]:
    rows = list_attachments(thread_id=thread_id, limit=2000)
    out = {}
    for item in rows:
        mid = item.get("message_id")
        if mid is None:
            continue
        out.setdefault(int(mid), []).append(item)
    return out


def export_thread_bundle(thread_id: int) -> dict | None:
    thread = get_thread(thread_id)
    if not thread:
        return None
    messages = list_messages(thread_id)
    attachments_map = get_attachments_map_for_thread(thread_id)
    for message in messages:
        message["attachments"] = attachments_map.get(int(message["id"]), [])
    return {
        "thread": thread,
        "messages": messages,
    }

