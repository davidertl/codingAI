#!/usr/bin/env python3
import os
import sys


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def main():
    repo_root = _repo_root()
    ai_agent_dir = os.path.join(repo_root, "ai-agent")
    if ai_agent_dir not in sys.path:
        sys.path.insert(0, ai_agent_dir)

    from core.projects_store import db_path, list_projects
    from main import get_available_repos, get_repo_policy_config

    repos = list(get_available_repos())
    default_modes = {}
    for repo in repos:
        policy = get_repo_policy_config(repo, force=False)
        default_modes[repo] = "manual" if bool(policy.get("approval", {}).get("required", False)) else "auto_10s"

    rows = list_projects(repos, default_push_gate_by_repo=default_modes)

    print(f"SQLite DB: {db_path()}")
    print(f"Projects synchronized: {len(rows)}")
    for row in rows:
        print(
            f"- {row['repo']}: enabled={row['enabled']} push_gate_mode={row['push_gate_mode']} "
            f"updated_at={row['updated_at']}"
        )


if __name__ == "__main__":
    main()
