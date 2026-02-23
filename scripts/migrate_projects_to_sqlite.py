#!/usr/bin/env python3
import json
import os
import sys


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _load_state(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    repo_root = _repo_root()
    ai_agent_dir = os.path.join(repo_root, "ai-agent")
    if ai_agent_dir not in sys.path:
        sys.path.insert(0, ai_agent_dir)

    from core.projects_store import db_path, migrate_from_state, list_projects
    from main import get_available_repos, load_state, get_repo_policy_config

    repos = list(get_available_repos())
    state = load_state()
    default_modes = {}
    for repo in repos:
        policy = get_repo_policy_config(repo, force=False)
        default_modes[repo] = "manual" if bool(policy.get("approval", {}).get("required", False)) else "auto_10s"

    migrate_from_state(repo_names=repos, state=state, default_push_gate_by_repo=default_modes)
    rows = list_projects(repos, default_push_gate_by_repo=default_modes)

    print(f"SQLite DB: {db_path()}")
    print(f"Migrated projects: {len(rows)}")
    for row in rows:
        print(
            f"- {row['repo']}: enabled={row['enabled']} push_gate_mode={row['push_gate_mode']} "
            f"updated_at={row['updated_at']}"
        )


if __name__ == "__main__":
    main()
