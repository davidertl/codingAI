import os
import threading
import time
import hashlib
import json
import asyncio
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.observability import (
    EVENT_LOG_FILE,
    get_metrics_snapshot,
    inc_counter,
    read_recent_events,
    record_event,
    render_prometheus_metrics,
    set_gauge,
    redact_dict,
)
from core.sandbox_runner_client import should_use_sandbox_mode
from core.projects_store import list_projects as list_projects_store
from core.projects_store import migrate_from_state as migrate_projects_from_state
from core.projects_store import set_project_push_gate_mode as set_project_push_gate_mode_store
from core.projects_store import set_project_enabled as set_project_enabled_store
from core.projects_store import set_project_labels as set_project_labels_store
from core.memory_store import (
    init_memory_tables,
    migrate_from_state as migrate_memory_from_state,
    list_strategy_memories,
    get_strategy_memory,
)
from core.rules_store import delete_rules as delete_rules_store
from core.rules_store import effective_rules as effective_rules_store
from core.rules_store import read_rules as read_rules_store
from core.rules_store import write_rules as write_rules_store
from core.research import research
from orchestrator.state_store import get_run as get_orchestrator_run
from orchestrator.state_store import list_artifacts as list_orchestrator_artifacts
from orchestrator.state_store import list_attempts as list_orchestrator_attempts
from orchestrator.state_store import list_runs as list_orchestrator_runs
from core.chat_store import (
    create_attachment as create_chat_attachment,
    create_message as create_chat_message,
    create_thread as create_chat_thread,
    delete_thread as delete_chat_thread,
    export_thread_bundle,
    get_message as get_chat_message,
    get_thread as get_chat_thread,
    list_attachments as list_chat_attachments,
    list_messages as list_chat_messages,
    list_threads as list_chat_threads,
    update_message as update_chat_message,
    update_thread as update_chat_thread,
)
import main
from github.ci_status import get_pr_ci_status
from github.issue_manager import get_ai_issues
from github.repo_manager import ensure_repo_mirror
from llm.provider import ensure_llm_ready, get_llm_runtime_status, get_local_model_status, iter_llm_chunks, record_llm_request_result
import llm.provider as llm_provider_runtime
from llm.chat_llm import chat_completion, chat_completion_stream, route_for_task
from paths import ENV_FILE, GITHUB_APP_PEM_FILE, setup_status
from github.app_auth import get_installation_token
from github.repo_manager import list_installation_repos

SERVICE_POLL_INTERVAL_SECONDS = int(os.getenv("SERVICE_POLL_INTERVAL_SECONDS", str(main.POLL_INTERVAL)))
LIVE_WS_PUSH_INTERVAL_SECONDS = max(2, int(os.getenv("LIVE_WS_PUSH_INTERVAL_SECONDS", "7") or "7"))
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
_TRUTHY = {"1", "true", "yes", "on"}
CHAT_STREAM_CHUNK_CHARS = max(8, int(os.getenv("CHAT_STREAM_CHUNK_CHARS", "120") or "120"))
CHAT_FILE_TREE_MAX_DEPTH = max(1, int(os.getenv("CHAT_FILE_TREE_MAX_DEPTH", "4") or "4"))
CHAT_FILE_SNIPPET_MAX_LINES = max(20, int(os.getenv("CHAT_FILE_SNIPPET_MAX_LINES", "240") or "240"))
CHAT_FILE_SNIPPET_MAX_CHARS = max(500, int(os.getenv("CHAT_FILE_SNIPPET_MAX_CHARS", "22000") or "22000"))
_ENV_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
_AUTO_PUSH_GATE_PATTERN = re.compile(r"^auto_(\d{1,6})s$")


def _utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_day_key():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _utc_week_key():
    now = datetime.now(timezone.utc)
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


class RepoWorker:
    def __init__(self, repo: str, poll_interval_seconds: int):
        self.repo = repo
        self.poll_interval_seconds = max(1, int(poll_interval_seconds))
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"codingai-worker-{repo}", daemon=True)
        self._lock = threading.Lock()
        self.started_at = _utc_now_iso()
        self.last_cycle_started_at = None
        self.last_cycle_finished_at = None
        self.last_error = ""
        self.cycles = 0

    def start(self):
        self._thread.start()

    def stop(self, timeout: float = 10.0):
        self._stop_event.set()
        self._thread.join(timeout=timeout)

    def is_running(self) -> bool:
        return self._thread.is_alive()

    def _run(self):
        while not self._stop_event.is_set():
            with self._lock:
                self.last_cycle_started_at = _utc_now_iso()

            try:
                main.run_repo_cycle_once(self.repo)
                err = ""
            except Exception as e:
                err = str(e)
                inc_counter("codingai_worker_errors_total", labels={"repo": self.repo})
                record_event(
                    "worker_cycle_error",
                    repo=self.repo,
                    status="error",
                    data={"error": err[:300]},
                )

            with self._lock:
                self.cycles += 1
                self.last_cycle_finished_at = _utc_now_iso()
                self.last_error = err[:1000]

            if self._stop_event.wait(self.poll_interval_seconds):
                break

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "repo": self.repo,
                "running": self.is_running(),
                "started_at": self.started_at,
                "last_cycle_started_at": self.last_cycle_started_at,
                "last_cycle_finished_at": self.last_cycle_finished_at,
                "last_error": self.last_error,
                "cycles": self.cycles,
                "poll_interval_seconds": self.poll_interval_seconds,
            }


class WorkerManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._workers: dict[str, RepoWorker] = {}
        self._auto_thread = threading.Thread(target=self._auto_scheduler, name="codingai-auto-scheduler", daemon=True)
        self._auto_thread.start()

    def _available_repos(self) -> list[str]:
        return main.get_available_repos()

    def start_repo(self, repo: str) -> dict:
        if repo not in self._available_repos():
            raise KeyError(repo)

        with self._lock:
            existing = self._workers.get(repo)
            if existing and existing.is_running():
                snap = existing.snapshot()
                snap["already_running"] = True
                return snap

            worker = RepoWorker(repo=repo, poll_interval_seconds=SERVICE_POLL_INTERVAL_SECONDS)
            self._workers[repo] = worker
            worker.start()
            snap = worker.snapshot()
            snap["already_running"] = False
            return snap

    def stop_repo(self, repo: str) -> dict:
        with self._lock:
            worker = self._workers.get(repo)
            if not worker:
                return {"repo": repo, "running": False, "stopped": False, "reason": "not_running"}

        worker.stop(timeout=10.0)
        with self._lock:
            self._workers.pop(repo, None)

        snap = worker.snapshot()
        snap["stopped"] = True
        return snap

    def list_workers(self) -> list[dict]:
        with self._lock:
            workers = list(self._workers.values())
        snapshots = [w.snapshot() for w in workers]
        set_gauge(
            "codingai_workers_running",
            len([w for w in snapshots if w.get("running")]),
        )
        return snapshots

    def is_repo_running(self, repo: str) -> bool:
        with self._lock:
            w = self._workers.get(repo)
            return bool(w and w.is_running())

    def stop_all(self):
        with self._lock:
            repos = list(self._workers.keys())
        for repo in repos:
            self.stop_repo(repo)

    def _auto_scheduler(self):
        while True:
            try:
                auto = main.load_state().get("automation", {}) or {}
                for repo, cfg in auto.items():
                    if not isinstance(cfg, dict):
                        continue
                    if not cfg.get("enabled"):
                        continue
                    if self.is_repo_running(repo):
                        continue
                    if repo not in self._available_repos():
                        continue
                    # start worker for automated repos
                    try:
                        self.start_repo(repo)
                        record_event("auto_start_worker", repo=repo, status="started")
                    except Exception as e:
                        record_event("auto_start_worker", repo=repo, status="error", data={"error": str(e)[:200]})
                # run periodic cleanup/self-check hooks
                from github.repo_manager import cleanup_jobs
                cleanup_jobs()
            except Exception:
                pass
            time.sleep(max(60, SERVICE_POLL_INTERVAL_SECONDS))


manager = WorkerManager()
app = FastAPI(title="CodingAI Control Plane", version="experimental-0.23.0")
app.mount("/ui/static", StaticFiles(directory=STATIC_DIR), name="ui-static")


def _require_repo(repo: str):
    repos = main.get_available_repos()
    if repo not in repos:
        raise HTTPException(status_code=404, detail=f"Unknown repo '{repo}'. Available: {repos}")


def _repo_state_summary(repo: str) -> list[dict]:
    state = main.load_state()
    repo_state = state.get(repo, {})
    out = []
    for key, value in repo_state.items():
        if not (isinstance(key, str) and key.isdigit() and isinstance(value, dict)):
            continue
        out.append(
            {
                "issue_number": int(key),
                "last_status": value.get("last_status"),
                "pr_created": bool(value.get("pr_created")),
                "pr_number": value.get("pr_number"),
                "pr_url": value.get("pr_url"),
                "check_run_url": value.get("check_run_url"),
                "report_comment_id": value.get("report_comment_id"),
                "patch_ops_count": value.get("patch_ops_count"),
                "patch_confidence": value.get("patch_confidence"),
                "test_patch_applied": bool(value.get("test_patch_applied")),
                "test_patch_ops_added": value.get("test_patch_ops_added"),
                "test_patch_confidence": value.get("test_patch_confidence"),
                "strategy_confidence_threshold": value.get("strategy_confidence_threshold"),
                "strategy_max_attempts": value.get("strategy_max_attempts"),
                "strategy_quarantine_threshold": value.get("strategy_quarantine_threshold"),
                "strategy_quarantine_seconds": value.get("strategy_quarantine_seconds"),
                "retry_after": value.get("retry_after", 0),
                "pending_manual_approval": bool(value.get("pending_manual_approval")),
                "ai_stopped": bool(value.get("ai_stopped")),
                "ai_stop_reason": value.get("ai_stop_reason"),
                "active_branch": value.get("active_branch"),
                "last_error": value.get("last_error"),
                "source_issue_updated_at": value.get("source_issue_updated_at"),
                "manual_prompt": value.get("manual_prompt"),
                "manual_prompt_updated_at": value.get("manual_prompt_updated_at"),
                "last_duration_ms": value.get("last_duration_ms"),
                "last_processed_at": value.get("last_processed_at"),
                "ci_gate": value.get("ci_gate"),
                "pipeline": value.get("pipeline", {}),
            }
        )
    out.sort(key=lambda x: x["issue_number"])
    return out


def _repo_budget_status(repo: str) -> dict:
    state = main.load_state()
    policy = main.get_repo_policy_config(repo, force=False)
    day_key = _utc_day_key()
    week_key = _utc_week_key()
    daily_count = int(state.get("daily_pr_counts", {}).get(repo, {}).get(day_key, 0))
    weekly_count = int(state.get("weekly_pr_counts", {}).get(repo, {}).get(week_key, 0))

    max_day = int(policy.get("pr", {}).get("max_per_day", 0))
    max_week = int(policy.get("pr", {}).get("max_per_week", 0))
    return {
        "day_key": day_key,
        "week_key": week_key,
        "daily_count": daily_count,
        "weekly_count": weekly_count,
        "max_per_day": max_day,
        "max_per_week": max_week,
        "daily_remaining": None if max_day <= 0 else max(0, max_day - daily_count),
        "weekly_remaining": None if max_week <= 0 else max(0, max_week - weekly_count),
    }


def _read_env_entries() -> dict:
    existing = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    existing[k] = v
    return existing


def _read_env_value(env_values: dict, key: str, default: str = "") -> str:
    from_file = str(env_values.get(key, "")).strip()
    if from_file:
        return from_file
    return str(os.getenv(key, default)).strip()


@app.get("/health")
def health():
    llm_ready = ensure_llm_ready(force=False)
    llm_runtime = get_llm_runtime_status()
    setup = setup_status()
    last_orchestrator_run = list_orchestrator_runs(limit=1)
    set_gauge("codingai_workers_running", len([w for w in manager.list_workers() if w.get("running")]))
    return {
        "status": "ok",
        "time_utc": _utc_now_iso(),
        "workers_running": len([w for w in manager.list_workers() if w.get("running")]),
        "llm_ready": bool(llm_ready.get("ready")),
        "llm_active_provider": llm_ready.get("active_provider"),
        "llm_requested_provider": llm_ready.get("requested_provider"),
        "llm_provider_chain": llm_ready.get("provider_chain", []),
        "llm_telemetry_file": llm_runtime.get("telemetry_file"),
        "setup_required": not bool(setup.get("setup_complete")),
        "setup": setup,
        "orchestrator_v2_enabled": bool(main.ORCHESTRATOR_V2_ENABLED),
        "runner_mode": "sandbox_api" if should_use_sandbox_mode() else "local_runner",
        "last_orchestrator_run": (last_orchestrator_run[0] if last_orchestrator_run else None),
    }


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    return render_prometheus_metrics()


@app.get("/setup/status")
def setup_status_api():
    return setup_status()


@app.get("/setup/values")
def setup_values_api():
    env_values = _read_env_entries()
    local_model_status = get_local_model_status(timeout=1.2)
    local_model_error = str(local_model_status.get("error") or "").strip()
    payload = {
        "owner": _read_env_value(env_values, "GITHUB_OWNER"),
        "app_id": _read_env_value(env_values, "GITHUB_APP_ID"),
        "installation_id": _read_env_value(env_values, "GITHUB_INSTALLATION_ID"),
        "llm_provider": _read_env_value(env_values, "LLM_PROVIDER", "openai"),
        "llm_provider_order": _read_env_value(env_values, "LLM_PROVIDER_ORDER", "local,openai"),
        "openai_base_url": _read_env_value(env_values, "OPENAI_BASE_URL", "https://api.openai.com"),
        "openai_api_key_set": bool(_read_env_value(env_values, "OPENAI_API_KEY")),
        "local_llm_base_url": _read_env_value(env_values, "LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434"),
        "local_llm_api_mode": _read_env_value(env_values, "LOCAL_LLM_API_MODE", "chat"),
        "local_llm_profile": _read_env_value(env_values, "LOCAL_LLM_PROFILE", "auto"),
        "local_llm_model": _read_env_value(env_values, "LOCAL_LLM_MODEL"),
        "local_llm_api_key_set": bool(_read_env_value(env_values, "LOCAL_LLM_API_KEY")),
        "local_models_count": int(local_model_status.get("models_count", 0)),
        "local_model_ready": bool(local_model_status.get("ready")),
    }
    if local_model_error:
        payload["local_model_error"] = local_model_error
    return payload


def _sanitize_env_key(key: str) -> str:
    normalized = str(key or "").strip().upper()
    if not normalized or not _ENV_KEY_PATTERN.match(normalized):
        raise ValueError(f"invalid env key: {key}")
    return normalized


def _sanitize_env_value(value: Any) -> str:
    normalized = str(value or "").strip()
    if any((ord(ch) < 32) or (ord(ch) == 127) for ch in normalized):
        raise ValueError("env values cannot contain control characters")
    return normalized


def _sync_process_env(entries: dict):
    for key, value in entries.items():
        normalized_key = _sanitize_env_key(key)
        normalized_value = _sanitize_env_value(value)
        if normalized_value:
            os.environ[normalized_key] = normalized_value
        else:
            os.environ.pop(normalized_key, None)


def _refresh_llm_runtime():
    llm_provider_runtime.refresh_runtime_config_from_env()
    ensure_llm_ready(force=True)


def _normalize_provider_name(value: str) -> str:
    provider = _sanitize_env_value(value).lower()
    if provider not in {"openai", "local", "auto"}:
        raise ValueError("llm_provider must be one of: openai, local, auto")
    return provider


def _normalize_provider_order(value: str) -> str:
    raw = _sanitize_env_value(value).lower()
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise ValueError("llm_provider_order must include one of: openai, local")
    out = []
    seen = set()
    for part in parts:
        if part not in {"openai", "local"}:
            raise ValueError("llm_provider_order can only contain: openai, local")
        if part not in seen:
            out.append(part)
            seen.add(part)
    return ",".join(out)


def _normalize_api_mode(value: str) -> str:
    mode = _sanitize_env_value(value).lower()
    if mode not in {"chat", "responses"}:
        raise ValueError("local_llm_api_mode must be chat or responses")
    return mode


def _normalize_local_llm_profile(value: str) -> str:
    profile = _sanitize_env_value(value).lower()
    if profile not in {"auto", "gpu16", "gpu24", "cpu"}:
        raise ValueError("local_llm_profile must be one of: auto, gpu16, gpu24, cpu")
    return profile


def _normalize_http_base_url(value: str, *, field_name: str) -> str:
    base_url = _sanitize_env_value(value).rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be an absolute http/https URL")
    return base_url


def _write_env_entries(entries: dict):
    existing = _read_env_entries()
    sanitized = {}
    for key, value in existing.items():
        sanitized[_sanitize_env_key(key)] = _sanitize_env_value(value)
    for key, value in entries.items():
        sanitized[_sanitize_env_key(key)] = _sanitize_env_value(value)
    lines = [f"{k}={v}\n" for k, v in sanitized.items()]
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines)


class ThreadCreatePayload(BaseModel):
    repo: str = Field(..., min_length=1, max_length=200)
    title: str = Field(default="", max_length=300)
    task_type: str = Field(default="chat", max_length=30)


class ThreadUpdatePayload(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    archived: bool | None = None
    task_type: str | None = Field(default=None, max_length=30)


class SnippetAttachmentPayload(BaseModel):
    repo: str | None = Field(default=None, max_length=200)
    path: str = Field(..., min_length=1, max_length=500)
    start_line: int = Field(default=1, ge=1)
    end_line: int = Field(default=120, ge=1)
    message_id: int | None = Field(default=None, ge=1)


class MessageCreatePayload(BaseModel):
    content: str = Field(..., min_length=1, max_length=20000)
    task_type: str | None = Field(default=None, max_length=30)
    stream: bool = False


class RulesPayload(BaseModel):
    rules_markdown: str = Field(default="", max_length=200000)
    attachment_ids: list[int] = Field(default_factory=list)
    snippet_attachments: list[SnippetAttachmentPayload] = Field(default_factory=list)


class PromptRerunPayload(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=12000)


def _normalize_chat_task_type(value: str | None) -> str:
    return str(route_for_task(value).get("task_type", "chat"))


def _chat_thread_or_404(thread_id: int) -> dict:
    thread = get_chat_thread(thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail=f"Unknown thread id {thread_id}")
    return thread


def _is_within_root(root: Path, target: Path) -> bool:
    root_real = root.resolve()
    target_real = target.resolve()
    return target_real == root_real or str(target_real).startswith(str(root_real) + os.sep)


def _repo_root_for_browser(repo: str) -> Path:
    repo_name = str(repo or "").strip()
    if not repo_name:
        raise HTTPException(status_code=400, detail="repo is required")
    try:
        root = Path(ensure_repo_mirror(repo_name)).resolve()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Unable to fetch repo mirror for '{repo_name}': {str(e)[:220]}")
    return root


def _resolve_repo_file_path(repo: str, rel_path: str) -> tuple[Path, Path]:
    root = _repo_root_for_browser(repo)
    safe_rel = str(rel_path or "").strip().replace("\\", "/")
    while safe_rel.startswith("./"):
        safe_rel = safe_rel[2:]
    safe_rel = safe_rel.lstrip("/")
    target = (root / safe_rel).resolve() if safe_rel else root
    if not _is_within_root(root, target):
        raise HTTPException(status_code=400, detail="invalid file path")
    return root, target


def _list_tree_node(root: Path, node: Path, *, depth: int, max_depth: int) -> dict:
    rel = str(node.relative_to(root)).replace(os.sep, "/") if node != root else ""
    if node.is_dir():
        out = {
            "path": rel,
            "name": node.name if rel else "/",
            "type": "dir",
            "children": [],
        }
        if depth < max_depth:
            children = []
            for child in sorted(node.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
                if child.name in {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}:
                    continue
                children.append(_list_tree_node(root, child, depth=depth + 1, max_depth=max_depth))
                if len(children) >= 300:
                    break
            out["children"] = children
        return out

    return {
        "path": rel,
        "name": node.name,
        "type": "file",
        "size": int(node.stat().st_size),
    }


def _read_file_snippet(*, repo: str, path: str, start_line: int, end_line: int) -> dict:
    root, target = _resolve_repo_file_path(repo, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    start = max(1, int(start_line))
    end = max(start, int(end_line))
    if (end - start + 1) > CHAT_FILE_SNIPPET_MAX_LINES:
        end = start + CHAT_FILE_SNIPPET_MAX_LINES - 1

    try:
        with open(target, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Unable to read file snippet: {e.strerror or str(e)}")

    if not lines:
        snippet = ""
        actual_end = start
    else:
        s_idx = min(len(lines), start) - 1
        e_idx = min(len(lines), end)
        selected = lines[s_idx:e_idx]
        snippet = "".join(selected)
        if len(snippet) > CHAT_FILE_SNIPPET_MAX_CHARS:
            snippet = snippet[:CHAT_FILE_SNIPPET_MAX_CHARS]
        actual_end = s_idx + len(selected)

    return {
        "repo": repo,
        "path": str(target.relative_to(root)).replace(os.sep, "/"),
        "start_line": start,
        "end_line": max(start, actual_end),
        "snippet": snippet,
    }


def _attachments_for_thread(thread_id: int) -> tuple[list[dict], dict[int, list[dict]]]:
    attachments = list_chat_attachments(thread_id=thread_id, limit=2000)
    by_message = {}
    for attachment in attachments:
        mid = attachment.get("message_id")
        if mid is None:
            continue
        by_message.setdefault(int(mid), []).append(attachment)
    return attachments, by_message


def _thread_messages_for_prompt(thread_id: int, *, include_until_message_id: int | None = None) -> list[dict]:
    messages = list_chat_messages(thread_id, limit=500)
    _all, by_message = _attachments_for_thread(thread_id)
    out = []
    for message in messages:
        msg_id = int(message["id"])
        if include_until_message_id is not None and msg_id > include_until_message_id:
            break
        role = str(message.get("role", "")).lower().strip()
        content = str(message.get("content", ""))
        if role == "assistant" and not content.strip():
            continue
        out.append(
            {
                "id": msg_id,
                "role": role,
                "content": content,
                "attachments": by_message.get(msg_id, []),
            }
        )
    return out


def _clone_attachments_to_message(thread_id: int, message_id: int, attachment_ids: list[int]) -> list[dict]:
    if not attachment_ids:
        return []
    all_rows = list_chat_attachments(thread_id=thread_id, limit=2000)
    row_map = {int(row["id"]): row for row in all_rows}
    out = []
    for aid in attachment_ids:
        row = row_map.get(int(aid))
        if not row:
            continue
        out.append(
            create_chat_attachment(
                thread_id=thread_id,
                message_id=message_id,
                repo=row.get("repo") or "",
                kind=row.get("kind") or "snippet",
                path=row.get("path") or "",
                start_line=int(row.get("start_line") or 1),
                end_line=int(row.get("end_line") or 1),
                snippet=row.get("snippet") or "",
                meta=row.get("meta") or {},
            )
        )
    return out


def _create_snippet_attachment(
    *,
    thread: dict,
    message_id: int | None,
    repo: str,
    path: str,
    start_line: int,
    end_line: int,
):
    snippet = _read_file_snippet(repo=repo, path=path, start_line=start_line, end_line=end_line)
    return create_chat_attachment(
        thread_id=int(thread["id"]),
        message_id=message_id,
        repo=snippet["repo"],
        kind="snippet",
        path=snippet["path"],
        start_line=int(snippet["start_line"]),
        end_line=int(snippet["end_line"]),
        snippet=snippet["snippet"],
        meta={"source": "file_snippet"},
    )


def _generate_assistant_for_message(assistant_message_id: int) -> dict:
    assistant = get_chat_message(assistant_message_id)
    if not assistant:
        raise HTTPException(status_code=404, detail=f"Unknown message id {assistant_message_id}")
    if str(assistant.get("role")) != "assistant":
        raise HTTPException(status_code=400, detail="stream is only supported for assistant messages")

    if str(assistant.get("status")) == "completed" and str(assistant.get("content", "")).strip():
        return assistant

    thread = _chat_thread_or_404(int(assistant["thread_id"]))
    meta = dict(assistant.get("meta") or {})
    route_task = _normalize_chat_task_type(meta.get("route_task_type") or assistant.get("task_type") or thread.get("task_type"))
    prompt_messages = _thread_messages_for_prompt(int(thread["id"]), include_until_message_id=int(assistant["id"]) - 1)
    llm = chat_completion(repo=str(thread["repo"]), messages=prompt_messages, task_type=route_task)

    if llm.get("ok"):
        updated = update_chat_message(
            int(assistant_message_id),
            content=str(llm.get("text") or ""),
            status="completed",
            model=str(llm.get("model") or ""),
            meta={
                **meta,
                "provider": llm.get("provider"),
                "route_task_type": llm.get("task_type"),
                "model": llm.get("model"),
                "generated_at": int(time.time()),
            },
        )
    else:
        err = str(llm.get("error") or "chat_failed")
        updated = update_chat_message(
            int(assistant_message_id),
            content=f"Assistant generation failed: {err}",
            status="failed",
            model=str(llm.get("model") or ""),
            meta={
                **meta,
                "provider": llm.get("provider"),
                "route_task_type": llm.get("task_type"),
                "model": llm.get("model"),
                "error": err,
                "generated_at": int(time.time()),
            },
        )
    return updated or get_chat_message(assistant_message_id)


def _stream_chunks(text: str, *, chunk_chars: int) -> list[str]:
    chunk_chars = max(8, int(chunk_chars))
    text = str(text or "")
    if not text:
        return [""]
    return [text[i:i + chunk_chars] for i in range(0, len(text), chunk_chars)]


@app.post("/setup/github")
def setup_github(
    owner: str = Form(...),
    app_id: str = Form(...),
    installation_id: str = Form(...),
):
    owner = owner.strip()[:200]
    app_id = app_id.strip()[:200]
    installation_id = installation_id.strip()[:200]
    if not (owner and app_id and installation_id):
        raise HTTPException(status_code=400, detail="owner, app_id, installation_id are required")
    updates = {
        "GITHUB_OWNER": owner,
        "GITHUB_APP_ID": app_id,
        "GITHUB_INSTALLATION_ID": installation_id,
    }
    try:
        _write_env_entries(updates)
        _sync_process_env(updates)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Unable to write env file: {e.strerror or str(e)}") from e
    record_event(
        "setup_github_updated",
        status="ok",
        data={
            "owner": owner[:120],
            "app_id_last4": app_id[-4:] if len(app_id) >= 4 else app_id,
            "installation_id_last4": installation_id[-4:] if len(installation_id) >= 4 else installation_id,
        },
    )
    return {"status": "ok", "setup": setup_status()}


@app.post("/setup/github/clear")
def setup_github_clear():
    updates = {
        "GITHUB_OWNER": "",
        "GITHUB_APP_ID": "",
        "GITHUB_INSTALLATION_ID": "",
    }
    try:
        _write_env_entries(updates)
        _sync_process_env(updates)
        manager.stop_all()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Unable to write env file: {e.strerror or str(e)}") from e

    record_event(
        "setup_github_cleared",
        status="ok",
        data={"owner": "cleared", "app_id": "cleared", "installation_id": "cleared"},
    )
    return {"status": "ok", "setup": setup_status(), "values": setup_values_api()}


@app.post("/setup/llm")
def setup_llm(
    llm_provider: str = Form(""),
    llm_provider_order: str = Form(""),
    openai_base_url: str = Form(""),
    openai_api_key: str = Form(""),
    openai_api_key_clear: bool = Form(False),
    local_llm_base_url: str = Form(""),
    local_llm_api_mode: str = Form(""),
    local_llm_profile: str = Form(""),
    local_llm_model: str = Form(""),
    local_llm_api_key: str = Form(""),
    local_llm_api_key_clear: bool = Form(False),
):
    env_values = _read_env_entries()
    current_provider = _read_env_value(env_values, "LLM_PROVIDER", "openai")
    current_provider_order = _read_env_value(env_values, "LLM_PROVIDER_ORDER", "local,openai")
    current_openai_base_url = _read_env_value(env_values, "OPENAI_BASE_URL", "https://api.openai.com")
    current_local_base_url = _read_env_value(env_values, "LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434")
    current_local_api_mode = _read_env_value(env_values, "LOCAL_LLM_API_MODE", "chat")
    current_local_profile = _read_env_value(env_values, "LOCAL_LLM_PROFILE", "auto")

    try:
        provider = _normalize_provider_name(llm_provider or current_provider or "openai")
        provider_order = _normalize_provider_order(llm_provider_order or current_provider_order or "local,openai")
        openai_base = _normalize_http_base_url(
            openai_base_url or current_openai_base_url or "https://api.openai.com",
            field_name="openai_base_url",
        )
        local_base = _normalize_http_base_url(
            local_llm_base_url or current_local_base_url or "http://127.0.0.1:11434",
            field_name="local_llm_base_url",
        )
        local_mode = _normalize_api_mode(local_llm_api_mode or current_local_api_mode or "chat")
        local_profile = _normalize_local_llm_profile(local_llm_profile or current_local_profile or "auto")
        local_model = _sanitize_env_value(local_llm_model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    updates = {
        "LLM_PROVIDER": provider,
        "LLM_PROVIDER_ORDER": provider_order,
        "OPENAI_BASE_URL": openai_base,
        "LOCAL_LLM_BASE_URL": local_base,
        "LOCAL_LLM_API_MODE": local_mode,
        "LOCAL_LLM_PROFILE": local_profile,
        "LOCAL_LLM_MODEL": local_model,
    }
    openai_key_action = "unchanged"
    local_key_action = "unchanged"
    if openai_api_key_clear:
        updates["OPENAI_API_KEY"] = ""
        openai_key_action = "cleared"
    elif str(openai_api_key or "").strip():
        updates["OPENAI_API_KEY"] = str(openai_api_key).strip()
        openai_key_action = "set"

    if local_llm_api_key_clear:
        updates["LOCAL_LLM_API_KEY"] = ""
        local_key_action = "cleared"
    elif str(local_llm_api_key or "").strip():
        updates["LOCAL_LLM_API_KEY"] = str(local_llm_api_key).strip()
        local_key_action = "set"

    try:
        _write_env_entries(updates)
        _sync_process_env(updates)
        _refresh_llm_runtime()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Unable to write env file: {e.strerror or str(e)}") from e

    record_event(
        "setup_llm_updated",
        status="ok",
        data={
            "llm_provider": provider,
            "llm_provider_order": provider_order,
            "openai_base_url": openai_base,
            "local_llm_base_url": local_base,
            "local_llm_api_mode": local_mode,
            "local_llm_profile": local_profile,
            "local_llm_model": local_model[:120],
            "openai_api_key": openai_key_action,
            "local_llm_api_key": local_key_action,
        },
    )
    return {
        "status": "ok",
        "setup": setup_status(),
        "values": setup_values_api(),
        "llm": ensure_llm_ready(force=False),
    }


@app.post("/setup/pem")
async def setup_pem(pem: UploadFile = File(...)):
    content = await pem.read()
    if not content or len(content) > 20000:
        raise HTTPException(status_code=400, detail="PEM content invalid or too large")
    if b"BEGIN" not in content or b"PRIVATE KEY" not in content:
        raise HTTPException(status_code=400, detail="PEM format not recognized")
    overwrite_allowed = os.getenv("SETUP_PEM_OVERWRITE", "true").strip().lower() in _TRUTHY
    pem_exists = GITHUB_APP_PEM_FILE.exists()
    if pem_exists and not overwrite_allowed:
        raise HTTPException(
            status_code=409,
            detail=(
                f"PEM already exists at {GITHUB_APP_PEM_FILE}. "
                "Overwrite blocked by SETUP_PEM_OVERWRITE=false."
            ),
        )
    try:
        os.makedirs(GITHUB_APP_PEM_FILE.parent, exist_ok=True)
        with open(GITHUB_APP_PEM_FILE, "wb") as f:
            f.write(content)
        os.chmod(GITHUB_APP_PEM_FILE, 0o600)
    except OSError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to save PEM at {GITHUB_APP_PEM_FILE}: {e.strerror or str(e)}",
        ) from e
    fingerprint = hashlib.sha256(content).hexdigest()
    record_event(
        "setup_pem_uploaded",
        status="ok",
        data={
            "pem_path": str(GITHUB_APP_PEM_FILE),
            "pem_size": len(content),
            "fingerprint_prefix": fingerprint[:16],
            "replaced_existing": pem_exists,
        },
    )
    return {"status": "ok", "fingerprint": fingerprint, "setup": setup_status()}


_SETUP_AUDIT_EVENT_PREFIXES = (
    "setup_github_",
    "setup_llm_",
    "setup_pem_",
    "setup_github_cleared",
    "rules_updated",
    "rules_deleted",
    "prompt_safety_block",
    "project_add",
    "project_remove",
    "project_update",
)


@app.get("/setup/audit")
def setup_audit(limit: int = 200):
    """Return the most recent setup-related audit events from the event log."""
    limit = max(1, min(limit, 2000))
    events: list[dict] = []
    log_path = EVENT_LOG_FILE
    if not os.path.isfile(log_path):
        return {"events": [], "total": 0}
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                ev = entry.get("event", "")
                if any(ev.startswith(p) for p in _SETUP_AUDIT_EVENT_PREFIXES) or ev in _SETUP_AUDIT_EVENT_PREFIXES:
                    events.append(entry)
    except OSError:
        return {"events": [], "total": 0, "error": "unable to read event log"}
    # Return most recent entries (tail)
    tail = events[-limit:] if len(events) > limit else events
    return {"events": list(reversed(tail)), "total": len(events)}


def _project_repo_candidates():
    source = "installation"
    error = None
    try:
        token = get_installation_token()
        repos = list_installation_repos(token)
    except Exception as e:
        source = "fallback"
        error = str(e)
        repos = main.get_available_repos()

    seen = set()
    out = []
    for repo in repos:
        name = str(repo or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out, source, error


def _project_default_push_gate_modes(repos: list[str]) -> dict[str, str]:
    out = {}
    for repo in repos:
        policy = main.get_repo_policy_config(repo, force=False, apply_project_push_gate=False)
        required = bool(policy.get("approval", {}).get("required", False))
        out[repo] = "manual" if required else "auto_10s"
    return out


def _project_rows(repos: list[str]):
    state = main.load_state()
    state_enabled_repos = state.get("projects_enabled") if isinstance(state.get("projects_enabled"), list) else None
    defaults = _project_default_push_gate_modes(repos)
    migrate_projects_from_state(repo_names=repos, state=state, default_push_gate_by_repo=defaults)
    # Migrate strategy memory from state.json → SQLite (idempotent)
    init_memory_tables()
    migrate_memory_from_state(state)
    return list_projects_store(repos, state_enabled_repos=state_enabled_repos, default_push_gate_by_repo=defaults)


def _validate_project_repo(repo: str, repos: list[str]):
    if repo not in repos:
        raise HTTPException(status_code=404, detail=f"Unknown project repo '{repo}'. Available: {repos}")


@app.get("/projects")
def projects():
    repos, source, error = _project_repo_candidates()
    rows = _project_rows(repos)
    projects = [
        {
            "repo": row["repo"],
            "enabled": bool(row["enabled"]),
            "push_gate_mode": str(row.get("push_gate_mode", "auto_10s")),
        }
        for row in rows
    ]
    return {
        "time_utc": _utc_now_iso(),
        "projects": projects,
        "source": source,
        "error": error,
    }


def _set_project_enabled(repo: str, enabled: bool):
    repos, _source, _error = _project_repo_candidates()
    _validate_project_repo(repo, repos)
    _project_rows(repos)
    set_project_enabled_store(repo, enabled)
    rows = _project_rows(repos)
    enabled_repos = [row["repo"] for row in rows if row["enabled"]]
    return {"repo": repo, "enabled": bool(enabled), "projects_enabled": enabled_repos}


@app.post("/projects/{repo}/enable")
def enable_project(repo: str):
    return _set_project_enabled(repo, True)


@app.post("/projects/{repo}/disable")
def disable_project(repo: str):
    return _set_project_enabled(repo, False)


@app.post("/projects/{repo}/push-gate")
def set_project_push_gate(repo: str, mode: str):
    repos, _source, _error = _project_repo_candidates()
    _validate_project_repo(repo, repos)
    normalized = str(mode or "").strip().lower()
    if normalized != "manual":
        match = _AUTO_PUSH_GATE_PATTERN.match(normalized)
        if not match:
            raise HTTPException(status_code=400, detail="mode must be 'manual' or 'auto_<seconds>s'")
        seconds = int(match.group(1))
        if seconds < 1 or seconds > 86400:
            raise HTTPException(status_code=400, detail="auto gate seconds must be in range 1..86400")
        normalized = f"auto_{seconds}s"
    _project_rows(repos)
    normalized = set_project_push_gate_mode_store(repo, normalized)
    record_event(
        "project_push_gate_updated",
        repo=repo,
        status="ok",
        data={"push_gate_mode": normalized},
    )
    return {"repo": repo, "push_gate_mode": normalized}


class ProjectLabelsPayload(BaseModel):
    labels: list[str] = Field(default_factory=lambda: ["ai-fix"])


@app.put("/projects/{repo}/labels")
def set_project_labels(repo: str, payload: ProjectLabelsPayload):
    repos, _source, _error = _project_repo_candidates()
    _validate_project_repo(repo, repos)
    labels = [str(l).strip() for l in payload.labels if str(l).strip()]
    if not labels:
        labels = ["ai-fix"]
    saved = set_project_labels_store(repo, labels)
    record_event(
        "project_labels_updated", repo=repo, status="ok",
        data={"labels": saved},
    )
    return {"repo": repo, "labels": saved}


# ── Strategy Memory endpoints ──────────────────────────────────────

@app.get("/strategy-memory")
def strategy_memory_list():
    """Return all strategy memory rows for diagnostics / UI."""
    return {"time_utc": _utc_now_iso(), "memories": list_strategy_memories()}


@app.get("/strategy-memory/{repo:path}")
def strategy_memory_get(repo: str):
    """Return strategy memory for a single repo."""
    repo = repo.strip()
    if not repo:
        raise HTTPException(status_code=400, detail="repo is required")
    return {"time_utc": _utc_now_iso(), "repo": repo, "memory": get_strategy_memory(repo)}


# ── Rules helpers ──────────────────────────────────────────────────

def _rules_event_data(*, scope: str, rules_markdown: str, repo: str | None = None) -> dict:
    body = str(rules_markdown or "")
    sha = hashlib.sha256(body.encode("utf-8", errors="ignore")).hexdigest()[:16]
    data = {
        "scope": scope,
        "bytes": len(body.encode("utf-8", errors="ignore")),
        "sha256_prefix": sha,
    }
    if repo:
        data["repo"] = repo
    return data


def _read_rules(scope: str, repo: str | None = None) -> dict:
    try:
        return read_rules_store(scope, repo=repo)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON in rules file: {e}") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unable to read rules: {e}") from e


def _write_rules(scope: str, rules_markdown: str, repo: str | None = None) -> dict:
    try:
        return write_rules_store(scope, rules_markdown=rules_markdown, repo=repo)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unable to write rules: {e}") from e


def _delete_rules(scope: str, repo: str | None = None) -> bool:
    try:
        return delete_rules_store(scope, repo=repo)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unable to delete rules: {e}") from e


@app.get("/rules/global")
def get_rules_global():
    return {"time_utc": _utc_now_iso(), "rules": _read_rules("global")}


@app.put("/rules/global")
def put_rules_global(payload: RulesPayload):
    rules = _write_rules("global", payload.rules_markdown, repo=None)
    record_event("rules_updated", status="ok", data=_rules_event_data(scope="global", rules_markdown=rules["rules_markdown"]))
    return {"time_utc": _utc_now_iso(), "rules": rules}


@app.get("/rules/projects/{repo}")
def get_rules_project(repo: str):
    return {"time_utc": _utc_now_iso(), "rules": _read_rules("projects", repo=repo)}


@app.put("/rules/projects/{repo}")
def put_rules_project(repo: str, payload: RulesPayload):
    rules = _write_rules("projects", payload.rules_markdown, repo=repo)
    record_event(
        "rules_updated",
        repo=repo,
        status="ok",
        data=_rules_event_data(scope="projects", repo=repo, rules_markdown=rules["rules_markdown"]),
    )
    return {"time_utc": _utc_now_iso(), "rules": rules}


@app.delete("/rules/projects/{repo}")
def delete_rules_project(repo: str):
    deleted = _delete_rules("projects", repo=repo)
    record_event("rules_deleted", repo=repo, status="ok", data={"scope": "projects", "deleted": deleted})
    return {"time_utc": _utc_now_iso(), "scope": "projects", "repo": repo, "deleted": deleted}


@app.get("/rules/workers/{repo}")
def get_rules_worker(repo: str):
    return {"time_utc": _utc_now_iso(), "rules": _read_rules("workers", repo=repo)}


@app.put("/rules/workers/{repo}")
def put_rules_worker(repo: str, payload: RulesPayload):
    rules = _write_rules("workers", payload.rules_markdown, repo=repo)
    record_event(
        "rules_updated",
        repo=repo,
        status="ok",
        data=_rules_event_data(scope="workers", repo=repo, rules_markdown=rules["rules_markdown"]),
    )
    return {"time_utc": _utc_now_iso(), "rules": rules}


@app.delete("/rules/workers/{repo}")
def delete_rules_worker(repo: str):
    deleted = _delete_rules("workers", repo=repo)
    record_event("rules_deleted", repo=repo, status="ok", data={"scope": "workers", "deleted": deleted})
    return {"time_utc": _utc_now_iso(), "scope": "workers", "repo": repo, "deleted": deleted}


@app.get("/rules/effective")
def get_rules_effective(repo: str = Query(..., min_length=1, max_length=200)):
    try:
        result = effective_rules_store(repo)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON in rules file: {e}") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unable to compute effective rules: {e}") from e
    return {
        "time_utc": _utc_now_iso(),
        "repo": str(result.get("repo") or repo),
        "text": str(result.get("text") or ""),
        "sources": result.get("sources", []),
    }


@app.get("/chat/threads")
def chat_threads(repo: str | None = None, include_archived: bool = False, limit: int = 100):
    rows = list_chat_threads(repo=repo, include_archived=bool(include_archived), limit=max(1, min(int(limit), 500)))
    return {
        "time_utc": _utc_now_iso(),
        "threads": rows,
    }


@app.post("/chat/threads")
def chat_create_thread(payload: ThreadCreatePayload):
    thread = create_chat_thread(
        repo=str(payload.repo).strip(),
        title=str(payload.title or "").strip(),
        task_type=_normalize_chat_task_type(payload.task_type),
    )
    record_event(
        "chat_thread_created",
        repo=thread["repo"],
        status="ok",
        data={"thread_id": thread["id"], "task_type": thread["task_type"]},
    )
    return {
        "time_utc": _utc_now_iso(),
        "thread": thread,
    }


@app.get("/chat/threads/{thread_id}")
def chat_get_thread(thread_id: int):
    bundle = export_thread_bundle(int(thread_id))
    if not bundle:
        raise HTTPException(status_code=404, detail=f"Unknown thread id {thread_id}")
    return {
        "time_utc": _utc_now_iso(),
        "thread": bundle.get("thread"),
        "messages": bundle.get("messages", []),
    }


@app.patch("/chat/threads/{thread_id}")
def chat_update_thread(thread_id: int, payload: ThreadUpdatePayload):
    updated = update_chat_thread(
        int(thread_id),
        title=payload.title,
        archived=payload.archived,
        task_type=_normalize_chat_task_type(payload.task_type) if payload.task_type is not None else None,
    )
    if not updated:
        raise HTTPException(status_code=404, detail=f"Unknown thread id {thread_id}")
    record_event(
        "chat_thread_updated",
        repo=updated["repo"],
        status="ok",
        data={"thread_id": updated["id"], "archived": updated["archived"], "task_type": updated["task_type"]},
    )
    return {
        "time_utc": _utc_now_iso(),
        "thread": updated,
    }


@app.delete("/chat/threads/{thread_id}")
def chat_delete_thread(thread_id: int):
    thread = get_chat_thread(int(thread_id))
    if not thread:
        raise HTTPException(status_code=404, detail=f"Unknown thread id {thread_id}")
    ok = delete_chat_thread(int(thread_id))
    record_event(
        "chat_thread_deleted",
        repo=thread["repo"],
        status="ok" if ok else "not_found",
        data={"thread_id": int(thread_id)},
    )
    return {"status": "ok" if ok else "not_found", "thread_id": int(thread_id)}


@app.get("/chat/threads/{thread_id}/messages")
def chat_thread_messages(thread_id: int, limit: int = 300):
    thread = _chat_thread_or_404(int(thread_id))
    messages = list_chat_messages(int(thread_id), limit=max(1, min(int(limit), 1000)))
    attachments, by_message = _attachments_for_thread(int(thread_id))
    for item in messages:
        item["attachments"] = by_message.get(int(item["id"]), [])
    return {
        "time_utc": _utc_now_iso(),
        "thread": thread,
        "messages": messages,
        "attachments_count": len(attachments),
    }


@app.get("/chat/threads/{thread_id}/attachments")
def chat_thread_attachments(thread_id: int, message_id: int | None = None, limit: int = 500):
    thread = _chat_thread_or_404(int(thread_id))
    rows = list_chat_attachments(
        thread_id=int(thread_id),
        message_id=int(message_id) if message_id is not None else None,
        limit=max(1, min(int(limit), 1000)),
    )
    return {
        "time_utc": _utc_now_iso(),
        "thread": thread,
        "attachments": rows,
    }


@app.post("/chat/threads/{thread_id}/attachments/snippet")
def chat_thread_attach_snippet(thread_id: int, payload: SnippetAttachmentPayload):
    thread = _chat_thread_or_404(int(thread_id))
    message_id = int(payload.message_id) if payload.message_id is not None else None
    if message_id is not None:
        message = get_chat_message(message_id)
        if not message or int(message.get("thread_id", 0)) != int(thread_id):
            raise HTTPException(status_code=404, detail=f"Unknown message id {message_id} for thread {thread_id}")
    repo = str(payload.repo or thread.get("repo") or "").strip()
    attachment = _create_snippet_attachment(
        thread=thread,
        message_id=message_id,
        repo=repo,
        path=str(payload.path),
        start_line=int(payload.start_line),
        end_line=int(payload.end_line),
    )
    record_event(
        "chat_attachment_created",
        repo=thread.get("repo"),
        status="ok",
        data={
            "thread_id": int(thread_id),
            "message_id": message_id,
            "attachment_id": attachment["id"],
            "path": attachment["path"],
        },
    )
    return {
        "time_utc": _utc_now_iso(),
        "attachment": attachment,
    }


@app.post("/chat/threads/{thread_id}/messages")
def chat_thread_send_message(thread_id: int, payload: MessageCreatePayload):
    thread = _chat_thread_or_404(int(thread_id))
    route = route_for_task(payload.task_type or thread.get("task_type"))
    route_task_type = str(route.get("task_type", "chat"))

    user_message = create_chat_message(
        thread_id=int(thread_id),
        role="user",
        content=str(payload.content or ""),
        status="completed",
        task_type=route_task_type,
        model="",
        meta={"source": "api"},
    )

    created_attachments = []
    created_attachments.extend(
        _clone_attachments_to_message(
            int(thread_id),
            int(user_message["id"]),
            [int(x) for x in payload.attachment_ids if int(x) > 0],
        )
    )
    for snippet_payload in payload.snippet_attachments:
        repo = str(snippet_payload.repo or thread.get("repo") or "").strip()
        created_attachments.append(
            _create_snippet_attachment(
                thread=thread,
                message_id=int(user_message["id"]),
                repo=repo,
                path=str(snippet_payload.path),
                start_line=int(snippet_payload.start_line),
                end_line=int(snippet_payload.end_line),
            )
        )

    assistant_message = create_chat_message(
        thread_id=int(thread_id),
        role="assistant",
        content="",
        status="pending",
        task_type=route_task_type,
        model=str(route.get("model") or ""),
        meta={
            "route_task_type": route_task_type,
            "route_model": str(route.get("model") or ""),
            "attachment_ids": [int(a["id"]) for a in created_attachments],
            "queued_at": int(time.time()),
        },
    )

    if not bool(payload.stream):
        assistant_message = _generate_assistant_for_message(int(assistant_message["id"]))
        record_event(
            "chat_message_completed",
            repo=thread.get("repo"),
            status="ok" if str(assistant_message.get("status")) == "completed" else "failed",
            data={
                "thread_id": int(thread_id),
                "user_message_id": int(user_message["id"]),
                "assistant_message_id": int(assistant_message["id"]),
                "route_task_type": route_task_type,
            },
        )
    else:
        record_event(
            "chat_message_queued",
            repo=thread.get("repo"),
            status="queued",
            data={
                "thread_id": int(thread_id),
                "user_message_id": int(user_message["id"]),
                "assistant_message_id": int(assistant_message["id"]),
                "route_task_type": route_task_type,
            },
        )

    return {
        "time_utc": _utc_now_iso(),
        "thread": thread,
        "stream": bool(payload.stream),
        "user_message": {**user_message, "attachments": created_attachments},
        "assistant_message": assistant_message,
        "stream_endpoint": f"/chat/messages/{int(assistant_message['id'])}/stream",
        "route": {"task_type": route_task_type, "model": route.get("model")},
    }


@app.get("/chat/messages/{message_id}/stream")
async def chat_stream_message(request: Request, message_id: int, last_event_id: int | None = Query(default=None)):
    assistant = get_chat_message(int(message_id))
    if not assistant:
        raise HTTPException(status_code=404, detail=f"Unknown message id {message_id}")
    if str(assistant.get("role")) != "assistant":
        raise HTTPException(status_code=400, detail="stream is only supported for assistant messages")

    header_last_id = request.headers.get("last-event-id")
    resume_from = int(last_event_id or 0)
    if resume_from <= 0 and header_last_id:
        try:
            resume_from = max(0, int(header_last_id))
        except Exception:
            resume_from = 0

    _sse_headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }

    # ---- Already-completed message: replay stored content as fake chunks ----
    if str(assistant.get("status")) == "completed" and str(assistant.get("content", "")).strip():
        chunks = _stream_chunks(str(assistant.get("content", "")), chunk_chars=CHAT_STREAM_CHUNK_CHARS)
        done_event_id = len(chunks) + 1

        async def _replay_stream():
            start_from = max(1, resume_from + 1)
            for idx, chunk in enumerate(chunks, start=1):
                if idx < start_from:
                    continue
                payload = {
                    "message_id": int(message_id),
                    "chunk_index": idx,
                    "chunk_total": len(chunks),
                    "delta": chunk,
                    "done": False,
                }
                yield f"id: {idx}\nevent: chunk\ndata: {json.dumps(payload, ensure_ascii=True)}\n\n"
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.012)
            if done_event_id >= start_from:
                done_payload = {
                    "message_id": int(message_id),
                    "done": True,
                    "status": "completed",
                }
                yield f"id: {done_event_id}\nevent: done\ndata: {json.dumps(done_payload, ensure_ascii=True)}\n\n"

        return StreamingResponse(_replay_stream(), media_type="text/event-stream", headers=_sse_headers)

    # ---- Pending message: attempt true LLM streaming ----
    thread = _chat_thread_or_404(int(assistant["thread_id"]))
    meta = dict(assistant.get("meta") or {})
    route_task = _normalize_chat_task_type(
        meta.get("route_task_type") or assistant.get("task_type") or thread.get("task_type"),
    )
    prompt_messages = _thread_messages_for_prompt(
        int(thread["id"]), include_until_message_id=int(assistant["id"]) - 1,
    )

    stream_result = chat_completion_stream(
        repo=str(thread["repo"]), messages=prompt_messages, task_type=route_task,
    )

    # -- Streaming init failed → emit single done-with-error event ----
    if not stream_result.get("ok"):
        err = str(stream_result.get("error") or "chat_failed")
        err_text = str(stream_result.get("text") or f"Assistant generation failed: {err}")
        update_chat_message(
            int(message_id),
            content=err_text,
            status="failed",
            model=str(stream_result.get("model") or ""),
            meta={**meta, "provider": stream_result.get("provider"), "error": err,
                  "generated_at": int(time.time())},
        )
        error_payload = {"message_id": int(message_id), "done": True, "status": "failed", "error": err}

        async def _error_stream():
            yield f"id: 1\nevent: done\ndata: {json.dumps(error_payload, ensure_ascii=True)}\n\n"

        return StreamingResponse(_error_stream(), media_type="text/event-stream", headers=_sse_headers)

    # -- Non-streaming fallback (endpoint doesn't support stream) → save & replay --
    if "text" in stream_result and "response" not in stream_result:
        text = str(stream_result.get("text") or "")
        update_chat_message(
            int(message_id),
            content=text,
            status="completed",
            model=str(stream_result.get("model") or ""),
            meta={**meta, "provider": stream_result.get("provider"),
                  "model": stream_result.get("model"),
                  "generated_at": int(time.time())},
        )
        fb_chunks = _stream_chunks(text, chunk_chars=CHAT_STREAM_CHUNK_CHARS)
        fb_done_id = len(fb_chunks) + 1

        async def _fallback_stream():
            for idx, chunk in enumerate(fb_chunks, start=1):
                payload = {
                    "message_id": int(message_id),
                    "chunk_index": idx,
                    "chunk_total": len(fb_chunks),
                    "delta": chunk,
                    "done": False,
                }
                yield f"id: {idx}\nevent: chunk\ndata: {json.dumps(payload, ensure_ascii=True)}\n\n"
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.012)
            done_payload = {"message_id": int(message_id), "done": True, "status": "completed"}
            yield f"id: {fb_done_id}\nevent: done\ndata: {json.dumps(done_payload, ensure_ascii=True)}\n\n"

        return StreamingResponse(_fallback_stream(), media_type="text/event-stream", headers=_sse_headers)

    # ---- True LLM streaming ----
    llm_response = stream_result["response"]
    llm_model = str(stream_result.get("model") or "")
    llm_provider = stream_result.get("provider")

    async def _live_stream():
        loop = asyncio.get_event_loop()
        accumulated: list[str] = []
        chunk_idx = 0
        gen = iter_llm_chunks(llm_response)
        sentinel = object()

        while True:
            if await request.is_disconnected():
                break
            # Run blocking next() in thread pool so we don't block the event loop
            delta = await loop.run_in_executor(None, next, gen, sentinel)
            if delta is sentinel:
                break
            chunk_idx += 1
            accumulated.append(delta)
            payload = {
                "message_id": int(message_id),
                "chunk_index": chunk_idx,
                "delta": delta,
                "done": False,
            }
            yield f"id: {chunk_idx}\nevent: chunk\ndata: {json.dumps(payload, ensure_ascii=True)}\n\n"

        full_text = "".join(accumulated).strip()
        if full_text:
            update_chat_message(
                int(message_id),
                content=full_text,
                status="completed",
                model=llm_model,
                meta={**meta, "provider": llm_provider, "model": llm_model,
                      "generated_at": int(time.time()), "streamed": True},
            )
            record_llm_request_result(
                provider=llm_provider or "", operation="chat_stream", ok=True,
            )
            done_payload = {"message_id": int(message_id), "done": True, "status": "completed"}
        else:
            update_chat_message(
                int(message_id),
                content="Assistant generation failed: empty response stream",
                status="failed",
                model=llm_model,
                meta={**meta, "provider": llm_provider, "error": "empty_stream",
                      "generated_at": int(time.time())},
            )
            record_llm_request_result(
                provider=llm_provider or "", operation="chat_stream", ok=False, error="empty_stream",
            )
            done_payload = {"message_id": int(message_id), "done": True, "status": "failed", "error": "empty_stream"}

        yield f"id: {chunk_idx + 1}\nevent: done\ndata: {json.dumps(done_payload, ensure_ascii=True)}\n\n"

    return StreamingResponse(_live_stream(), media_type="text/event-stream", headers=_sse_headers)


@app.get("/chat/files/{repo}/tree")
def chat_files_tree(repo: str, path: str = "", depth: int = 2):
    root, target = _resolve_repo_file_path(repo, path)
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail=f"Directory not found: {path}")
    depth = max(1, min(int(depth), CHAT_FILE_TREE_MAX_DEPTH))
    tree = _list_tree_node(root, target, depth=0, max_depth=depth)
    return {
        "time_utc": _utc_now_iso(),
        "repo": repo,
        "path": str(target.relative_to(root)).replace(os.sep, "/") if target != root else "",
        "max_depth": depth,
        "tree": tree,
    }


@app.get("/chat/files/{repo}/snippet")
def chat_file_snippet(repo: str, path: str, start_line: int = 1, end_line: int = 120):
    snippet = _read_file_snippet(repo=repo, path=path, start_line=int(start_line), end_line=int(end_line))
    return {
        "time_utc": _utc_now_iso(),
        **snippet,
    }


def _get_issue_state_or_404(repo: str, issue_number: int):
    state = main.load_state()
    repo_state = state.get(repo, {})
    issue_state = repo_state.get(str(issue_number))
    if not issue_state:
        raise HTTPException(status_code=404, detail=f"No state recorded for issue {issue_number} in {repo}")
    return state, issue_state


@app.get("/pipeline/{repo}/{issue_number}")
def pipeline(repo: str, issue_number: int):
    _require_repo(repo)
    state, issue_state = _get_issue_state_or_404(repo, issue_number)
    pipeline = issue_state.get("pipeline") or {}
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline state not available")
    return {
        "repo": repo,
        "issue": issue_number,
        "pipeline": pipeline,
        "time_utc": _utc_now_iso(),
    }


@app.get("/pipeline/{repo}/{issue_number}/diff")
def pipeline_diff(repo: str, issue_number: int):
    _require_repo(repo)
    _state, issue_state = _get_issue_state_or_404(repo, issue_number)
    pipeline = issue_state.get("pipeline") or {}
    diff = pipeline.get("diff", "")
    return {
        "repo": repo,
        "issue": issue_number,
        "diff": diff,
        "length": len(diff),
        "time_utc": _utc_now_iso(),
    }


def _pipeline_control(repo: str, issue_number: int, *, action: str, reason: str | None = None):
    state, issue_state = _get_issue_state_or_404(repo, issue_number)
    pipeline = issue_state.setdefault("pipeline", {})
    now = int(time.time())
    if action == "pause":
        pipeline["pause_requested"] = True
        pipeline["paused"] = True
        pipeline["paused_at"] = now
        pipeline.setdefault("pause_deadline", now + main.PAUSE_WINDOW_SECONDS)
    elif action == "resume":
        pipeline["pause_requested"] = False
        pipeline["paused"] = False
        pipeline["pause_deadline"] = now + 3
    elif action == "cancel":
        pipeline["cancel_requested"] = True
        if reason:
            pipeline["cancel_reason"] = reason[:200]
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action {action}")
    pipeline["updated_at"] = now
    main.save_state(state)
    return {"status": "ok", "pipeline": pipeline, "time_utc": _utc_now_iso()}


@app.post("/pipeline/{repo}/{issue_number}/pause")
def pipeline_pause(repo: str, issue_number: int):
    _require_repo(repo)
    return _pipeline_control(repo, issue_number, action="pause")


@app.post("/pipeline/{repo}/{issue_number}/resume")
def pipeline_resume(repo: str, issue_number: int):
    _require_repo(repo)
    return _pipeline_control(repo, issue_number, action="resume")


@app.post("/pipeline/{repo}/{issue_number}/cancel")
def pipeline_cancel(repo: str, issue_number: int, reason: str | None = None):
    _require_repo(repo)
    return _pipeline_control(repo, issue_number, action="cancel", reason=reason)


@app.post("/pipeline/{repo}/{issue_number}/prompt-rerun")
def pipeline_prompt_rerun(repo: str, issue_number: int, payload: PromptRerunPayload):
    _require_repo(repo)
    prompt = str(payload.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")

    state, issue_state = _get_issue_state_or_404(repo, issue_number)
    now = int(time.time())
    issue_state["manual_prompt"] = prompt
    issue_state["manual_prompt_updated_at"] = now
    issue_state["force_reprocess_once"] = True
    issue_state["force_bypass_cooldown_once"] = True
    pipeline = issue_state.setdefault("pipeline", {})
    pipeline["manual_prompt_updated_at"] = now
    pipeline["manual_prompt_preview"] = prompt[:240]
    pipeline["updated_at"] = now
    main.save_state(state)

    record_event(
        "manual_prompt_rerun_requested",
        repo=repo,
        issue_number=issue_number,
        status="started",
        data={"prompt_chars": len(prompt)},
    )

    started = time.time()
    try:
        main.run_repo_issue_once(repo, issue_number)
    except Exception as e:
        duration_ms = int((time.time() - started) * 1000)
        record_event(
            "manual_prompt_rerun_finished",
            repo=repo,
            issue_number=issue_number,
            status="error",
            duration_ms=duration_ms,
            data={"error": str(e)[:280]},
        )
        raise HTTPException(status_code=500, detail=f"prompt rerun failed: {str(e)[:280]}")

    duration_ms = int((time.time() - started) * 1000)
    record_event(
        "manual_prompt_rerun_finished",
        repo=repo,
        issue_number=issue_number,
        status="ok",
        duration_ms=duration_ms,
        data={"prompt_chars": len(prompt)},
    )
    return {
        "status": "ok",
        "repo": repo,
        "issue": issue_number,
        "prompt_saved": True,
        "duration_ms": duration_ms,
        "time_utc": _utc_now_iso(),
    }


@app.get("/research")
def research_query(query: str, max_results: int | None = None, use_cache: bool = True):
    if not query or not query.strip():
        raise HTTPException(status_code=400, detail="query is required")
    try:
        data = research(query.strip(), max_results=max_results, use_cache=use_cache)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:400])
    return {
        "time_utc": _utc_now_iso(),
        "query": data.get("query"),
        "cached": data.get("cached", False),
        "results": data.get("results", []),
        "summary": data.get("summary", ""),
        "search_error": data.get("search_error"),
        "summary_error": data.get("summary_error"),
    }


@app.get("/metrics/json")
def metrics_json():
    return {
        "time_utc": _utc_now_iso(),
        "metrics": get_metrics_snapshot(),
    }


@app.post("/self-checks")
def run_self_checks():
    try:
        main.self_checks()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])
    return {"status": "ok", "time_utc": _utc_now_iso()}


@app.get("/events")
def events(limit: int = 100, repo: str | None = None, event: str | None = None):
    limit = max(1, min(int(limit), 1000))
    items = read_recent_events(limit=limit, repo=repo, event=event)
    items = [redact_dict(i) if isinstance(i, dict) else i for i in items]
    return {
        "time_utc": _utc_now_iso(),
        "count": len(items),
        "events": items,
    }


@app.get("/orchestration/runs")
def orchestration_runs(repo: str | None = None, limit: int = 50):
    rows = list_orchestrator_runs(repo=repo, limit=max(1, min(int(limit), 500)))
    return {
        "time_utc": _utc_now_iso(),
        "count": len(rows),
        "runs": rows,
    }


@app.get("/orchestration/runs/{run_id}")
def orchestration_run(run_id: str, attempts_limit: int = 300):
    run = get_orchestrator_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown orchestrator run '{run_id}'")
    attempts = list_orchestrator_attempts(run_id, limit=max(1, min(int(attempts_limit), 2000)))
    return {
        "time_utc": _utc_now_iso(),
        "run": run,
        "attempts": attempts,
    }


@app.get("/orchestration/runs/{run_id}/artifacts")
def orchestration_run_artifacts(run_id: str, limit: int = 200):
    run = get_orchestrator_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown orchestrator run '{run_id}'")
    artifacts = list_orchestrator_artifacts(run_id, limit=max(1, min(int(limit), 2000)))
    return {
        "time_utc": _utc_now_iso(),
        "run_id": run_id,
        "count": len(artifacts),
        "artifacts": artifacts,
    }


@app.get("/", include_in_schema=False)
def ui_root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/ui", include_in_schema=False)
def ui_alias():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return PlainTextResponse("", status_code=204)


@app.get("/repos")
def repos():
    available = main.get_available_repos()
    auto = main.load_state().get("automation", {})
    return [
        {
            "repo": repo,
            "running": manager.is_repo_running(repo),
            "automation": bool(auto.get(repo, {}).get("enabled", False)) if isinstance(auto, dict) else False,
        }
        for repo in available
    ]


def _automation_state():
    state = main.load_state()
    return state.setdefault("automation", {})


@app.get("/automation")
def automation():
    auto = _automation_state()
    return {"time_utc": _utc_now_iso(), "automation": auto}


def _set_automation(repo: str, enabled: bool, push_gate: str | None = None):
    state = main.load_state()
    auto = state.setdefault("automation", {})
    cfg = auto.get(repo, {}) if isinstance(auto.get(repo), dict) else {}
    cfg["enabled"] = enabled
    if push_gate:
        cfg["push_gate"] = push_gate
    cfg["updated_at"] = int(time.time())
    auto[repo] = cfg
    main.save_state(state)
    return cfg


@app.post("/automation/{repo}/enable")
def automation_enable(repo: str, push_gate: str | None = None):
    _require_repo(repo)
    cfg = _set_automation(repo, True, push_gate)
    return {"status": "ok", "repo": repo, "automation": cfg}


@app.post("/automation/{repo}/disable")
def automation_disable(repo: str):
    _require_repo(repo)
    cfg = _set_automation(repo, False)
    return {"status": "ok", "repo": repo, "automation": cfg}


@app.get("/policies")
def policies(force: bool = False):
    snapshot = main.get_policies_config(force=force)
    return {
        "time_utc": _utc_now_iso(),
        "available_repos": main.get_available_repos(),
        "policies": snapshot,
    }


@app.get("/policy/{repo}")
def repo_policy(repo: str, force: bool = False):
    _require_repo(repo)
    policy = main.get_repo_policy_config(repo, force=force)
    return {
        "repo": repo,
        "time_utc": _utc_now_iso(),
        "policy": policy,
        "budget": _repo_budget_status(repo),
    }


@app.get("/queue/{repo}")
def queue(repo: str):
    _require_repo(repo)
    try:
        issues = get_ai_issues(repo)
        queue_items = []
        for issue in issues:
            queue_items.append(
                {
                    "number": issue.get("number"),
                    "title": issue.get("title"),
                    "html_url": issue.get("html_url"),
                    "updated_at": issue.get("updated_at"),
                    "labels": [
                        l.get("name")
                        for l in issue.get("labels", [])
                        if isinstance(l, dict) and l.get("name")
                    ],
                }
            )
        return {"repo": repo, "count": len(queue_items), "issues": queue_items, "error": ""}
    except Exception as e:
        return {"repo": repo, "count": 0, "issues": [], "error": str(e)[:500]}


@app.get("/ci/{repo}/{pr_number}")
def ci_status(repo: str, pr_number: int):
    _require_repo(repo)
    started = time.time()
    try:
        summary = get_pr_ci_status(repo, int(pr_number))
    except Exception as e:
        inc_counter("codingai_ci_gate_total", labels={"repo": repo, "result": "api_error"})
        raise HTTPException(status_code=502, detail=f"CI status lookup failed: {str(e)[:280]}")
    duration_ms = int((time.time() - started) * 1000)
    record_event(
        "api_ci_status_lookup",
        repo=repo,
        status="ok",
        duration_ms=duration_ms,
        data={"pr_number": int(pr_number)},
    )
    return {
        "repo": repo,
        "time_utc": _utc_now_iso(),
        "ci_status": summary,
        "duration_ms": duration_ms,
    }


@app.get("/repo/{repo}/summary")
def repo_summary(repo: str):
    _require_repo(repo)
    queue_data = queue(repo)
    workers = manager.list_workers()
    worker = next((w for w in workers if w.get("repo") == repo), None)
    policy = main.get_repo_policy_config(repo, force=False)
    return {
        "repo": repo,
        "time_utc": _utc_now_iso(),
        "worker": worker,
        "queue": queue_data,
        "tracked_issues": _repo_state_summary(repo),
        "policy": policy,
        "budget": _repo_budget_status(repo),
    }


def _empty_repo_summary(repo: str | None = None, *, error: str = "") -> dict:
    return {
        "repo": str(repo or ""),
        "time_utc": _utc_now_iso(),
        "worker": None,
        "queue": {"repo": str(repo or ""), "count": 0, "issues": [], "error": str(error or "")[:500]},
        "tracked_issues": [],
        "policy": {},
        "budget": {},
    }


def _live_rules_snapshot(repo: str | None) -> dict:
    global_rules = _read_rules("global")
    if not repo:
        return {
            "global": global_rules,
            "project": None,
            "worker": None,
            "effective": {
                "repo": "",
                "text": str(global_rules.get("rules_markdown") or ""),
                "sources": [global_rules],
            },
        }

    project_rules = _read_rules("projects", repo=repo)
    worker_rules = _read_rules("workers", repo=repo)
    try:
        effective = effective_rules_store(repo)
    except Exception:
        effective = {
            "repo": repo,
            "text": "",
            "sources": [global_rules, project_rules, worker_rules],
        }
    return {
        "global": global_rules,
        "project": project_rules,
        "worker": worker_rules,
        "effective": {
            "repo": str(effective.get("repo") or repo),
            "text": str(effective.get("text") or ""),
            "sources": effective.get("sources", []),
        },
    }


def _live_snapshot(selected_repo: str | None = None) -> dict:
    health_payload = health()
    repos_payload = repos()
    repo_names = [
        str(item.get("repo") or "").strip()
        for item in repos_payload
        if isinstance(item, dict) and str(item.get("repo") or "").strip()
    ]
    if selected_repo and selected_repo in repo_names:
        resolved_repo = selected_repo
    else:
        resolved_repo = repo_names[0] if repo_names else None

    projects_payload = projects()
    workers_payload = workers()
    setup_status_payload = setup_status_api()
    setup_values_payload = setup_values_api()

    if resolved_repo:
        try:
            summary_payload = repo_summary(resolved_repo)
        except Exception as e:
            summary_payload = _empty_repo_summary(resolved_repo, error=f"summary failed: {str(e)[:260]}")
    else:
        summary_payload = _empty_repo_summary()

    return {
        "type": "snapshot",
        "time_utc": _utc_now_iso(),
        "selected_repo": resolved_repo,
        "health": health_payload,
        "repos": repos_payload,
        "projects": projects_payload,
        "workers": workers_payload,
        "setup_status": setup_status_payload,
        "setup_values": setup_values_payload,
        "summary": summary_payload,
        "rules": _live_rules_snapshot(resolved_repo),
        "orchestration_runs": list_orchestrator_runs(repo=resolved_repo, limit=20),
    }


async def _ws_handle_chat_send(websocket: WebSocket, payload: dict):
    """Handle a chat_send message over the live WebSocket — streams LLM response back."""
    thread_id = payload.get("thread_id")
    content = str(payload.get("content") or "").strip()
    task_type_raw = str(payload.get("task_type") or "chat").strip()
    nonce = payload.get("nonce", "")  # client-provided correlation id

    if not thread_id or not content:
        await websocket.send_json({
            "type": "chat_error", "nonce": nonce,
            "error": "thread_id and content are required",
            "time_utc": _utc_now_iso(),
        })
        return

    try:
        thread = get_chat_thread(int(thread_id))
        if not thread:
            raise ValueError(f"Unknown thread {thread_id}")

        route = route_for_task(task_type_raw or thread.get("task_type"))
        route_task_type = str(route.get("task_type", "chat"))

        user_message = create_chat_message(
            thread_id=int(thread_id), role="user", content=content,
            status="completed", task_type=route_task_type, model="", meta={"source": "ws"},
        )
        assistant_message = create_chat_message(
            thread_id=int(thread_id), role="assistant", content="", status="pending",
            task_type=route_task_type, model=str(route.get("model") or ""),
            meta={"route_task_type": route_task_type, "route_model": str(route.get("model") or ""),
                  "queued_at": int(time.time())},
        )
        assistant_id = int(assistant_message["id"])
        await websocket.send_json({
            "type": "chat_ack", "nonce": nonce, "thread_id": int(thread_id),
            "user_message": user_message, "assistant_message_id": assistant_id,
            "time_utc": _utc_now_iso(),
        })

        prompt_messages = _thread_messages_for_prompt(int(thread_id), include_until_message_id=assistant_id - 1)
        meta = dict(assistant_message.get("meta") or {})

        stream_result = chat_completion_stream(
            repo=str(thread.get("repo") or ""), messages=prompt_messages, task_type=route_task_type,
        )

        if not stream_result.get("ok"):
            err = str(stream_result.get("error") or "chat_failed")
            err_text = str(stream_result.get("text") or f"Assistant generation failed: {err}")
            update_chat_message(assistant_id, content=err_text, status="failed",
                                model=str(stream_result.get("model") or ""),
                                meta={**meta, "provider": stream_result.get("provider"), "error": err,
                                      "generated_at": int(time.time())})
            await websocket.send_json({
                "type": "chat_done", "nonce": nonce, "message_id": assistant_id,
                "status": "failed", "error": err, "time_utc": _utc_now_iso(),
            })
            return

        # Non-streaming fallback
        if "text" in stream_result and "response" not in stream_result:
            text = str(stream_result.get("text") or "")
            update_chat_message(assistant_id, content=text, status="completed",
                                model=str(stream_result.get("model") or ""),
                                meta={**meta, "provider": stream_result.get("provider"),
                                      "model": stream_result.get("model"), "generated_at": int(time.time())})
            await websocket.send_json({
                "type": "chat_chunk", "nonce": nonce, "message_id": assistant_id,
                "delta": text, "chunk_index": 1, "done": False, "time_utc": _utc_now_iso(),
            })
            await websocket.send_json({
                "type": "chat_done", "nonce": nonce, "message_id": assistant_id,
                "status": "completed", "time_utc": _utc_now_iso(),
            })
            return

        # True LLM streaming
        llm_response = stream_result["response"]
        llm_model = str(stream_result.get("model") or "")
        llm_provider = stream_result.get("provider")
        loop = asyncio.get_event_loop()
        accumulated: list[str] = []
        chunk_idx = 0
        gen = iter_llm_chunks(llm_response)
        sentinel = object()

        while True:
            delta = await loop.run_in_executor(None, next, gen, sentinel)
            if delta is sentinel:
                break
            chunk_idx += 1
            accumulated.append(delta)
            await websocket.send_json({
                "type": "chat_chunk", "nonce": nonce, "message_id": assistant_id,
                "delta": delta, "chunk_index": chunk_idx, "done": False,
                "time_utc": _utc_now_iso(),
            })

        full_text = "".join(accumulated).strip()
        status = "completed" if full_text else "failed"
        update_chat_message(assistant_id, content=full_text or "Empty response",
                            status=status, model=llm_model,
                            meta={**meta, "provider": llm_provider, "model": llm_model,
                                  "generated_at": int(time.time()), "streamed": True})
        if full_text:
            record_llm_request_result(provider=llm_provider or "", operation="chat_stream_ws", ok=True)
        else:
            record_llm_request_result(provider=llm_provider or "", operation="chat_stream_ws", ok=False, error="empty_stream")

        await websocket.send_json({
            "type": "chat_done", "nonce": nonce, "message_id": assistant_id,
            "status": status, "time_utc": _utc_now_iso(),
        })

    except Exception as e:
        await websocket.send_json({
            "type": "chat_error", "nonce": nonce,
            "error": str(e)[:500], "time_utc": _utc_now_iso(),
        })


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    await websocket.accept()
    selected_repo = None
    seq = 0
    await websocket.send_json(
        {
            "type": "hello",
            "time_utc": _utc_now_iso(),
            "interval_seconds": LIVE_WS_PUSH_INTERVAL_SECONDS,
        }
    )

    while True:
        raw_message = None
        try:
            raw_message = await asyncio.wait_for(websocket.receive_text(), timeout=LIVE_WS_PUSH_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            raw_message = None
        except WebSocketDisconnect:
            break
        except Exception:
            break

        if raw_message:
            try:
                payload = json.loads(raw_message)
            except Exception:
                await websocket.send_json(
                    {
                        "type": "error",
                        "time_utc": _utc_now_iso(),
                        "error": "invalid_json",
                    }
                )
                continue
            msg_type = str(payload.get("type") or "").strip().lower()
            if msg_type in {"set_repo", "select_repo", "subscribe"}:
                candidate = str(payload.get("repo") or "").strip()
                selected_repo = candidate or None
            elif msg_type == "ping":
                await websocket.send_json({"type": "pong", "time_utc": _utc_now_iso()})
            elif msg_type == "chat_send":
                # ── WebSocket chat streaming ────────────────────────
                await _ws_handle_chat_send(websocket, payload)
                continue  # skip snapshot push after chat stream

        try:
            snapshot = _live_snapshot(selected_repo)
        except Exception as e:
            await websocket.send_json(
                {
                    "type": "error",
                    "time_utc": _utc_now_iso(),
                    "error": f"snapshot_failed: {str(e)[:280]}",
                }
            )
            continue

        seq += 1
        snapshot["seq"] = seq
        await websocket.send_json(snapshot)


@app.get("/state")
def state():
    return {
        "time_utc": _utc_now_iso(),
        "available_repos": main.get_available_repos(),
        "workers": manager.list_workers(),
        "state": main.load_state(),
    }


@app.get("/workers")
def workers():
    return {
        "workers": manager.list_workers(),
    }


@app.post("/run/repo/{repo}")
def run_repo(repo: str):
    _require_repo(repo)
    try:
        snapshot = manager.start_repo(repo)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown repo '{repo}'")
    inc_counter("codingai_api_actions_total", labels={"action": "run_repo"})
    record_event("api_run_repo", repo=repo, status="started")

    return {
        "repo": repo,
        "running": True,
        "already_running": bool(snapshot.get("already_running")),
        "worker": snapshot,
    }


@app.post("/run-once/repo/{repo}")
def run_repo_once(repo: str):
    _require_repo(repo)
    started = time.time()
    try:
        main.run_repo_cycle_once(repo)
    except Exception as e:
        duration_ms = int((time.time() - started) * 1000)
        inc_counter("codingai_api_actions_total", labels={"action": "run_repo_once_error"})
        record_event(
            "api_run_repo_once",
            repo=repo,
            status="error",
            duration_ms=duration_ms,
            data={"error": str(e)[:280]},
        )
        raise HTTPException(status_code=500, detail=str(e)[:500])
    duration_ms = int((time.time() - started) * 1000)
    inc_counter("codingai_api_actions_total", labels={"action": "run_repo_once"})
    record_event("api_run_repo_once", repo=repo, status="ok", duration_ms=duration_ms)
    return {
        "repo": repo,
        "ran_once": True,
        "duration_ms": duration_ms,
    }


@app.post("/stop/repo/{repo}")
def stop_repo(repo: str):
    _require_repo(repo)
    snapshot = manager.stop_repo(repo)
    inc_counter("codingai_api_actions_total", labels={"action": "stop_repo"})
    record_event("api_stop_repo", repo=repo, status="stopped")
    return {
        "repo": repo,
        "running": False,
        "stopped": bool(snapshot.get("stopped")),
        "worker": snapshot,
    }


# ── Self-tasks ─────────────────────────────────────────────────────

from core.self_tasks import (
    list_self_tasks as _list_self_tasks,
    get_self_task as _get_self_task,
    snooze_self_task as _snooze_self_task,
    dismiss_self_task as _dismiss_self_task,
    run_self_task_scan as _run_self_task_scan,
)


@app.get("/self-tasks")
def self_tasks_list(status: str | None = Query(default=None), limit: int = Query(default=200)):
    tasks = _list_self_tasks(status=status, limit=min(int(limit), 2000))
    return {"time_utc": _utc_now_iso(), "count": len(tasks), "tasks": tasks}


@app.get("/self-tasks/{task_id}")
def self_tasks_get(task_id: int):
    task = _get_self_task(int(task_id))
    if not task:
        raise HTTPException(status_code=404, detail=f"Self-task {task_id} not found")
    return {"time_utc": _utc_now_iso(), "task": task}


@app.post("/self-tasks/{task_id}/snooze")
def self_tasks_snooze(task_id: int, seconds: int = Query(default=86400)):
    task = _snooze_self_task(int(task_id), seconds=int(seconds))
    if not task:
        raise HTTPException(status_code=404, detail=f"Self-task {task_id} not found")
    return {"time_utc": _utc_now_iso(), "task": task}


@app.post("/self-tasks/{task_id}/dismiss")
def self_tasks_dismiss(task_id: int):
    task = _dismiss_self_task(int(task_id))
    if not task:
        raise HTTPException(status_code=404, detail=f"Self-task {task_id} not found")
    return {"time_utc": _utc_now_iso(), "task": task}


@app.post("/self-tasks/scan")
def self_tasks_scan():
    result = _run_self_task_scan()
    return {"time_utc": _utc_now_iso(), **result}


@app.on_event("shutdown")
def _shutdown():
    manager.stop_all()
