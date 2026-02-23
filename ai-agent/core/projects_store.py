import json
import os
import sqlite3
import threading
import time
import re
from pathlib import Path

from paths import AI_AGENT_DIR

_DB_RAW = os.getenv("CODINGAI_DB_FILE", "").strip()
DB_FILE = Path(_DB_RAW).expanduser().resolve() if _DB_RAW else (AI_AGENT_DIR / "state.db").resolve()
_LOCK = threading.Lock()
_AUTO_PUSH_GATE_PATTERN = re.compile(r"^auto_(\d{1,6})s$")
_MIN_AUTO_GATE_SECONDS = 1
_MAX_AUTO_GATE_SECONDS = 86400


def db_path() -> str:
    return str(DB_FILE)


def _connect():
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_FILE), timeout=20, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
              repo TEXT PRIMARY KEY,
              enabled INTEGER NOT NULL DEFAULT 1,
              labels_json TEXT NOT NULL DEFAULT '[]',
              push_gate_mode TEXT NOT NULL DEFAULT 'auto_10s',
              policy_ref TEXT NOT NULL DEFAULT '',
              updated_at INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_projects_updated_at ON projects(updated_at)")
        conn.commit()


def _normalize_push_gate_mode(mode: str | None, *, default: str = "auto_10s") -> str:
    raw = str(mode or "").strip().lower()
    if raw == "manual":
        return "manual"
    m = _AUTO_PUSH_GATE_PATTERN.match(raw)
    if m:
        try:
            seconds = int(m.group(1))
        except ValueError:
            seconds = 10
        seconds = max(_MIN_AUTO_GATE_SECONDS, min(_MAX_AUTO_GATE_SECONDS, seconds))
        return f"auto_{seconds}s"
    return default


def _upsert_project(
    conn,
    repo: str,
    *,
    enabled: bool,
    labels_json: str = "[]",
    push_gate_mode: str = "auto_10s",
    policy_ref: str = "",
):
    conn.execute(
        """
        INSERT INTO projects(repo, enabled, labels_json, push_gate_mode, policy_ref, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(repo) DO UPDATE SET
          enabled=excluded.enabled,
          labels_json=excluded.labels_json,
          push_gate_mode=excluded.push_gate_mode,
          policy_ref=excluded.policy_ref,
          updated_at=excluded.updated_at
        """,
        (
            repo,
            1 if enabled else 0,
            labels_json,
            _normalize_push_gate_mode(push_gate_mode),
            str(policy_ref or ""),
            int(time.time()),
        ),
    )


def seed_projects_if_needed(
    repo_names: list[str],
    *,
<<<<<<< ui-notes-local-llm
=======
    state_enabled_repos: list[str] | None = None,
>>>>>>> localstate
    default_push_gate_by_repo: dict[str, str] | None = None,
):
    init_db()
    default_push_gate_by_repo = default_push_gate_by_repo or {}
    cleaned = [str(r).strip() for r in (repo_names or []) if str(r).strip()]
    if not cleaned:
        return

    with _LOCK, _connect() as conn:
        cur = conn.execute("SELECT repo, enabled, push_gate_mode FROM projects")
        existing_rows = cur.fetchall()
        existing = {str(row["repo"]): row for row in existing_rows}
<<<<<<< ui-notes-local-llm
=======
        state_enabled_set = set(str(r).strip() for r in (state_enabled_repos or []) if str(r).strip())
        explicit_state_seed = state_enabled_repos is not None
        empty_table = len(existing) == 0
>>>>>>> localstate

        for repo in cleaned:
            if repo in existing:
                continue
<<<<<<< ui-notes-local-llm
=======
            enabled = (repo in state_enabled_set) if explicit_state_seed else True
>>>>>>> localstate
            default_mode = _normalize_push_gate_mode(default_push_gate_by_repo.get(repo), default="auto_10s")
            _upsert_project(
                conn,
                repo,
                enabled=True,
                labels_json="[]",
                push_gate_mode=default_mode,
                policy_ref="",
            )

        conn.commit()


def list_projects(
    repo_names: list[str],
    *,
<<<<<<< ui-notes-local-llm
=======
    state_enabled_repos: list[str] | None = None,
>>>>>>> localstate
    default_push_gate_by_repo: dict[str, str] | None = None,
) -> list[dict]:
    seed_projects_if_needed(
        repo_names,
<<<<<<< ui-notes-local-llm
=======
        state_enabled_repos=state_enabled_repos,
>>>>>>> localstate
        default_push_gate_by_repo=default_push_gate_by_repo,
    )
    if not repo_names:
        return []

    order = {str(repo): i for i, repo in enumerate(repo_names)}
    with _LOCK, _connect() as conn:
        cur = conn.execute(
            "SELECT repo, enabled, labels_json, push_gate_mode, policy_ref, updated_at FROM projects"
        )
        rows = cur.fetchall()

    row_map = {str(row["repo"]): row for row in rows}
    out = []
    for repo in repo_names:
        row = row_map.get(repo)
        if row is None:
            out.append(
                {
                    "repo": repo,
                    "enabled": True,
                    "labels": [],
                    "push_gate_mode": _normalize_push_gate_mode(
                        (default_push_gate_by_repo or {}).get(repo), default="auto_10s"
                    ),
                    "policy_ref": "",
                    "updated_at": int(time.time()),
                }
            )
            continue
        try:
            labels = json.loads(row["labels_json"] or "[]")
            if not isinstance(labels, list):
                labels = []
        except Exception:
            labels = []
        out.append(
            {
                "repo": repo,
                "enabled": bool(int(row["enabled"])),
                "labels": labels,
                "push_gate_mode": _normalize_push_gate_mode(row["push_gate_mode"], default="auto_10s"),
                "policy_ref": str(row["policy_ref"] or ""),
                "updated_at": int(row["updated_at"] or 0),
            }
        )
    out.sort(key=lambda x: order.get(x["repo"], 10**9))
    return out


def set_project_enabled(repo: str, enabled: bool):
    init_db()
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT labels_json, push_gate_mode, policy_ref FROM projects WHERE repo = ?",
            (repo,),
        ).fetchone()
        labels_json = str(row["labels_json"]) if row else "[]"
        push_gate_mode = _normalize_push_gate_mode(row["push_gate_mode"] if row else "auto_10s")
        policy_ref = str(row["policy_ref"]) if row else ""
        _upsert_project(
            conn,
            repo,
            enabled=bool(enabled),
            labels_json=labels_json,
            push_gate_mode=push_gate_mode,
            policy_ref=policy_ref,
        )
        conn.commit()


def set_project_push_gate_mode(repo: str, mode: str):
    mode = _normalize_push_gate_mode(mode)
    init_db()
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT enabled, labels_json, policy_ref FROM projects WHERE repo = ?",
            (repo,),
        ).fetchone()
        enabled = bool(int(row["enabled"])) if row else True
        labels_json = str(row["labels_json"]) if row else "[]"
        policy_ref = str(row["policy_ref"]) if row else ""
        _upsert_project(
            conn,
            repo,
            enabled=enabled,
            labels_json=labels_json,
            push_gate_mode=mode,
            policy_ref=policy_ref,
        )
        conn.commit()
    return mode


def get_project_push_gate_mode(repo: str, *, default: str = "auto_10s") -> str:
    init_db()
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT push_gate_mode FROM projects WHERE repo = ?", (repo,)).fetchone()
    if not row:
        return _normalize_push_gate_mode(default)
    return _normalize_push_gate_mode(row["push_gate_mode"], default=default)
<<<<<<< ui-notes-local-llm
=======


def migrate_from_state(
    *,
    repo_names: list[str],
    state: dict | None,
    default_push_gate_by_repo: dict[str, str] | None = None,
):
    state = state or {}
    state_enabled_repos = state.get("projects_enabled") if isinstance(state, dict) else None
    if state_enabled_repos is not None and not isinstance(state_enabled_repos, list):
        state_enabled_repos = None
    seed_projects_if_needed(
        repo_names,
        state_enabled_repos=state_enabled_repos,
        default_push_gate_by_repo=default_push_gate_by_repo,
    )
>>>>>>> localstate
