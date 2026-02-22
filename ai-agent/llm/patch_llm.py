import json
import os
import posixpath
import re
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv("/home/codingai/ai-agent/.env")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_PATCH_MODEL = os.getenv("OPENAI_PATCH_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))

_SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    "venv",
    ".venv",
    "dist",
    "build",
    "target",
}
_TEXT_EXTS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".json",
    ".yml",
    ".yaml",
    ".md",
    ".txt",
    ".sh",
    ".env",
    ".cs",
    ".csproj",
    ".sln",
    ".sql",
    ".html",
    ".css",
    ".toml",
    ".ini",
    ".xml",
    ".dockerignore",
}
_PRIORITY_NAMES = {
    "README.md",
    "docker-compose.yml",
    "Dockerfile",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
}


def _extract_json(text: str) -> dict:
    if not text:
        raise ValueError("Empty model response")

    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        raise ValueError(f"No JSON object found in model output: {text[:300]}")

    return json.loads(m.group(0))


def _should_include_file(name: str) -> bool:
    if name in _PRIORITY_NAMES:
        return True
    _, ext = os.path.splitext(name)
    return ext.lower() in _TEXT_EXTS


def _collect_repo_context(
    repo_path: str,
    *,
    max_files: int = 30,
    max_chars_per_file: int = 1800,
    max_total_chars: int = 30000,
) -> dict:
    candidates: list[tuple[int, str]] = []

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            if not _should_include_file(name):
                continue

            abs_path = os.path.join(root, name)
            rel_path = os.path.relpath(abs_path, repo_path).replace("\\", "/")
            priority = 0 if name in _PRIORITY_NAMES else 1
            candidates.append((priority, rel_path))

    candidates.sort(key=lambda x: (x[0], x[1]))

    context_files = []
    total_chars = 0

    for _, rel_path in candidates:
        if len(context_files) >= max_files:
            break
        if total_chars >= max_total_chars:
            break

        abs_path = os.path.join(repo_path, rel_path)
        try:
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            continue

        snippet = content[:max_chars_per_file]
        if not snippet.strip():
            continue

        remaining = max_total_chars - total_chars
        if remaining <= 0:
            break
        if len(snippet) > remaining:
            snippet = snippet[:remaining]

        context_files.append(
            {
                "path": rel_path,
                "snippet": snippet,
            }
        )
        total_chars += len(snippet)

    return {
        "file_count": len(context_files),
        "files": context_files,
    }


def _normalize_path(path: Any) -> str:
    if not isinstance(path, str):
        raise ValueError("Patch op path must be a string.")

    p = path.replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    p = posixpath.normpath(p)

    if not p or p in {".", "/"}:
        raise ValueError("Patch op path must not be empty.")
    if p.startswith("/") or p.startswith("../") or "/../" in f"/{p}":
        raise ValueError(f"Unsafe patch op path: {path}")
    if p == ".git" or p.startswith(".git/"):
        raise ValueError("Patch op path must not target .git internals.")

    return p


def _validate_patch_ops(
    patch_ops: Any,
    *,
    max_ops: int = 20,
    max_file_chars: int = 120000,
    max_total_chars: int = 400000,
) -> list[dict]:
    if not isinstance(patch_ops, list):
        raise ValueError("patch_ops must be a list.")
    if not patch_ops:
        raise ValueError("patch_ops must not be empty.")
    if len(patch_ops) > max_ops:
        raise ValueError(f"patch_ops exceeds max_ops={max_ops}.")

    validated = []
    seen_paths = set()
    total_chars = 0

    for idx, op in enumerate(patch_ops):
        if not isinstance(op, dict):
            raise ValueError(f"patch_ops[{idx}] must be an object.")

        action = op.get("action")
        if action not in {"create", "update", "delete"}:
            raise ValueError(f"patch_ops[{idx}].action is invalid.")

        path = _normalize_path(op.get("path"))
        if path in seen_paths:
            raise ValueError(f"Duplicate patch path: {path}")
        seen_paths.add(path)

        normalized = {
            "path": path,
            "action": action,
        }

        if action in {"create", "update"}:
            content = op.get("content")
            if not isinstance(content, str):
                raise ValueError(f"patch_ops[{idx}].content must be a string for action={action}.")
            if len(content) > max_file_chars:
                raise ValueError(f"patch_ops[{idx}].content exceeds max_file_chars={max_file_chars}.")
            normalized["content"] = content
            total_chars += len(content)
        else:
            if "content" in op and op.get("content") not in (None, ""):
                raise ValueError(f"patch_ops[{idx}].content must be omitted for action=delete.")

        if total_chars > max_total_chars:
            raise ValueError(f"Total content exceeds max_total_chars={max_total_chars}.")

        validated.append(normalized)

    return validated


def _parse_confidence(v: Any) -> float:
    try:
        conf = float(v)
    except Exception:
        conf = 0.0
    return max(0.0, min(1.0, conf))


def propose_patch_ops(
    *,
    repo_path: str,
    repo_name: str,
    issue: dict,
    repo_analysis: dict,
    max_ops: int = 20,
) -> dict:
    if not OPENAI_API_KEY:
        return {
            "patch_ops": [],
            "reason": "OPENAI_API_KEY not set; cannot generate patch ops.",
            "confidence": 0.0,
        }

    issue_number = issue.get("number")
    issue_title = str(issue.get("title", ""))
    issue_body = str(issue.get("body", ""))

    context = _collect_repo_context(repo_path)

    instructions = (
        "You are a coding patch generator.\n"
        "Given an issue and repository context, output ONLY JSON with this schema:\n"
        "{\n"
        '  "patch_ops": [\n'
        "    {\n"
        '      "path": "relative/path",\n'
        '      "action": "create|update|delete",\n'
        '      "content": "full file content for create/update"\n'
        "    }\n"
        "  ],\n"
        '  "reason": "short explanation",\n'
        '  "confidence": 0.0\n'
        "}\n"
        "Rules:\n"
        "- Output valid JSON only (no markdown).\n"
        "- Use paths relative to repository root.\n"
        "- Never use absolute paths or ../ traversal.\n"
        "- Do not touch .git internals.\n"
        "- Keep patch_ops small and focused.\n"
        "- content must be complete final file content for create/update.\n"
    )

    user_input = {
        "repo_name": repo_name,
        "issue": {
            "number": issue_number,
            "title": issue_title,
            "body": issue_body[:12000],
        },
        "repo_analysis": repo_analysis,
        "repo_context": context,
        "constraints": {
            "max_ops": max_ops,
        },
    }

    payload = {
        "model": OPENAI_PATCH_MODEL,
        "instructions": instructions,
        "input": json.dumps(user_input, ensure_ascii=False),
        "max_output_tokens": 3500,
        "text": {"format": {"type": "text"}},
    }

    r = requests.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=120,
    )

    if r.status_code >= 400:
        return {
            "patch_ops": [],
            "reason": f"OpenAI API error {r.status_code}; patch generation unavailable.",
            "confidence": 0.0,
        }

    try:
        data = r.json()
    except Exception:
        return {
            "patch_ops": [],
            "reason": "OpenAI API returned invalid JSON payload.",
            "confidence": 0.0,
        }

    output_text = data.get("output_text", "")
    try:
        out = _extract_json(output_text)
    except Exception:
        return {
            "patch_ops": [],
            "reason": "Model output was not valid JSON for patch ops.",
            "confidence": 0.0,
        }

    try:
        validated_ops = _validate_patch_ops(out.get("patch_ops"), max_ops=max_ops)
    except Exception as e:
        return {
            "patch_ops": [],
            "reason": f"Patch validation failed: {e}",
            "confidence": 0.0,
        }

    return {
        "patch_ops": validated_ops,
        "reason": str(out.get("reason", ""))[:500],
        "confidence": _parse_confidence(out.get("confidence", 0.0)),
    }
