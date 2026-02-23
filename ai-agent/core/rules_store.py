import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paths import AI_AGENT_DIR

_REPO_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,200}$")
_SCOPES = {"global", "projects", "workers"}
_RULES_ROOT = (AI_AGENT_DIR / ".agent" / "rules").resolve()


def rules_root() -> Path:
    return _RULES_ROOT


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_scope(scope: str) -> str:
    value = str(scope or "").strip().lower()
    if value not in _SCOPES:
        raise ValueError(f"invalid rules scope '{scope}'")
    return value


def _normalize_repo(repo: str | None) -> str:
    value = str(repo or "").strip()
    if not value or not _REPO_PATTERN.match(value):
        raise ValueError("repo must match ^[A-Za-z0-9._-]{1,200}$")
    return value


def _rules_path(scope: str, repo: str | None = None) -> Path:
    normalized_scope = _normalize_scope(scope)
    if normalized_scope == "global":
        return (_RULES_ROOT / "global.json").resolve()
    normalized_repo = _normalize_repo(repo)
    return (_RULES_ROOT / normalized_scope / f"{normalized_repo}.json").resolve()


def _normalize_markdown(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\r\n", "\n")
    if len(text) > 200_000:
        raise ValueError("rules_markdown too large (max 200000 chars)")
    return text


def _read_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("rules file must contain a JSON object")
    return data


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp-{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def read_rules(scope: str, repo: str | None = None) -> dict:
    normalized_scope = _normalize_scope(scope)
    normalized_repo = _normalize_repo(repo) if normalized_scope != "global" else None
    path = _rules_path(normalized_scope, repo=normalized_repo)
    if not path.exists():
        return {
            "scope": normalized_scope,
            "repo": normalized_repo,
            "exists": False,
            "updated_at_utc": "",
            "rules_markdown": "",
        }

    data = _read_json(path)
    updated_at_utc = str(data.get("updated_at_utc") or "").strip()
    rules_markdown = _normalize_markdown(data.get("rules_markdown", ""))
    return {
        "scope": normalized_scope,
        "repo": normalized_repo,
        "exists": True,
        "updated_at_utc": updated_at_utc,
        "rules_markdown": rules_markdown,
    }


def write_rules(scope: str, rules_markdown: str, repo: str | None = None) -> dict:
    path = _rules_path(scope, repo=repo)
    payload = {
        "version": 1,
        "updated_at_utc": _utc_now_iso(),
        "rules_markdown": _normalize_markdown(rules_markdown),
    }
    _atomic_write_json(path, payload)
    return read_rules(scope, repo=repo)


def delete_rules(scope: str, repo: str | None = None) -> bool:
    path = _rules_path(scope, repo=repo)
    if not path.exists():
        return False
    path.unlink()
    return True


def _section_lines(header: str, body: str) -> str:
    cleaned = _normalize_markdown(body).strip()
    if not cleaned:
        return ""
    return f"[{header}]\n{cleaned}"


def effective_rules(repo: str) -> dict:
    normalized_repo = _normalize_repo(repo)
    global_rules = read_rules("global")
    project_rules = read_rules("projects", repo=normalized_repo)
    worker_rules = read_rules("workers", repo=normalized_repo)

    sections = []
    if str(global_rules.get("rules_markdown", "")).strip():
        sections.append(_section_lines("GLOBAL", global_rules["rules_markdown"]))
    if str(project_rules.get("rules_markdown", "")).strip():
        sections.append(_section_lines("PROJECT", project_rules["rules_markdown"]))
    if str(worker_rules.get("rules_markdown", "")).strip():
        sections.append(_section_lines("WORKER", worker_rules["rules_markdown"]))

    return {
        "repo": normalized_repo,
        "text": "\n\n".join([s for s in sections if s]).strip(),
        "sources": [global_rules, project_rules, worker_rules],
    }
