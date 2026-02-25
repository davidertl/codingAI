#!/usr/bin/env python3
"""One-time migration: state.json -> state.db (SQLite).

Run from the repository root:
    python scripts/migrate_state_to_sqlite.py

The script reads ai-agent/state.json and inserts all repo/issue entries
into the SQLite database at ai-agent/state.db. It is safe to run multiple
times; existing rows are skipped (upsert on repo+issue_number).
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_JSON = REPO_ROOT / "ai-agent" / "state.json"
STATE_DB = REPO_ROOT / "ai-agent" / "state.db"


def ensure_schema(db: sqlite3.Connection):
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS issue_state (
            repo          TEXT NOT NULL,
            issue_number  INTEGER NOT NULL,
            data_json     TEXT NOT NULL DEFAULT '{}',
            pipeline_stage TEXT NOT NULL DEFAULT '',
            updated_at    REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (repo, issue_number)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS repo_meta (
            repo          TEXT PRIMARY KEY,
            data_json     TEXT NOT NULL DEFAULT '{}',
            updated_at    REAL NOT NULL DEFAULT 0
        )
    """)
    db.commit()


def migrate(state_json_path: Path, db_path: Path, *, verbose: bool = True):
    if not state_json_path.exists():
        print(f"No state.json found at {state_json_path}; nothing to migrate.")
        return

    with open(state_json_path, "r", encoding="utf-8") as f:
        state = json.load(f)

    if not isinstance(state, dict):
        print("state.json is not a dict; aborting.")
        return

    db = sqlite3.connect(str(db_path))
    ensure_schema(db)

    issue_count = 0
    repo_count = 0

    for repo_key, repo_value in state.items():
        if not isinstance(repo_value, dict):
            continue

        repo_meta = {}
        for key, val in repo_value.items():
            if isinstance(key, str) and key.isdigit() and isinstance(val, dict):
                issue_number = int(key)
                data_json = json.dumps(val, ensure_ascii=False)
                pipeline_stage = str(val.get("pipeline_stage") or "")
                db.execute("""
                    INSERT INTO issue_state (repo, issue_number, data_json, pipeline_stage, updated_at)
                    VALUES (?, ?, ?, ?, strftime('%s', 'now'))
                    ON CONFLICT(repo, issue_number) DO UPDATE SET
                        data_json = excluded.data_json,
                        pipeline_stage = excluded.pipeline_stage,
                        updated_at = excluded.updated_at
                """, (repo_key, issue_number, data_json, pipeline_stage))
                issue_count += 1
            else:
                repo_meta[key] = val

        if repo_meta:
            db.execute("""
                INSERT INTO repo_meta (repo, data_json, updated_at)
                VALUES (?, ?, strftime('%s', 'now'))
                ON CONFLICT(repo) DO UPDATE SET
                    data_json = excluded.data_json,
                    updated_at = excluded.updated_at
            """, (repo_key, json.dumps(repo_meta, ensure_ascii=False)))
            repo_count += 1

    db.commit()
    db.close()

    if verbose:
        print(f"Migration complete: {issue_count} issues, {repo_count} repos -> {db_path}")

    backup_path = state_json_path.with_suffix(".json.bak")
    if not backup_path.exists():
        import shutil
        shutil.copy2(state_json_path, backup_path)
        if verbose:
            print(f"Backup created: {backup_path}")


if __name__ == "__main__":
    migrate(STATE_JSON, STATE_DB)
