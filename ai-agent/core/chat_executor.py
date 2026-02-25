import logging
import os
import time
import uuid
from dataclasses import dataclass, field

from core.observability import record_event

log = logging.getLogger(__name__)


@dataclass
class ChatTaskContext:
    task_id: str = ""
    repo: str = ""
    message: str = ""
    history: list[dict] = field(default_factory=list)
    repo_path: str = ""
    repo_analysis: dict = field(default_factory=dict)
    file_tree: list[str] = field(default_factory=list)
    classification: dict = field(default_factory=dict)
    plan: dict = field(default_factory=dict)
    status: str = "pending"
    created_at: float = 0.0


@dataclass
class ChatTaskResult:
    task_id: str = ""
    status: str = "failed"
    reply: str = ""
    plan: dict = field(default_factory=dict)
    diff: str = ""
    patch_ops: list[dict] = field(default_factory=list)
    test_output: str = ""
    test_passed: bool = False
    error: str = ""
    duration_ms: int = 0


_ACTIVE_TASKS: dict[str, ChatTaskContext] = {}


def _assemble_context(
    *,
    repo: str,
    message: str,
    history: list[dict],
    repo_path: str,
) -> ChatTaskContext:
    from core.test_runner import analyze_repo
    from orchestrator.roles.classifier import classify_task

    task_id = f"chat-{uuid.uuid4().hex[:12]}"
    repo_analysis = {}
    file_tree: list[str] = []

    if repo_path and os.path.isdir(repo_path):
        try:
            repo_analysis = analyze_repo(repo_path) or {}
        except Exception:
            pass
        try:
            for root, dirs, files in os.walk(repo_path):
                dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__", ".venv", "venv"}]
                for f in files:
                    rel = os.path.relpath(os.path.join(root, f), repo_path).replace("\\", "/")
                    file_tree.append(rel)
                    if len(file_tree) >= 500:
                        break
                if len(file_tree) >= 500:
                    break
        except Exception:
            pass

    classification = classify_task(
        issue_title=message[:200],
        issue_body=message,
        labels=["chat-task"],
        repo_metadata=repo_analysis,
        file_tree_summary="\n".join(file_tree[:100]),
    ).model_dump()

    ctx = ChatTaskContext(
        task_id=task_id,
        repo=repo,
        message=message,
        history=list(history),
        repo_path=repo_path,
        repo_analysis=repo_analysis,
        file_tree=file_tree,
        classification=classification,
        status="context_assembled",
        created_at=time.time(),
    )
    _ACTIVE_TASKS[task_id] = ctx
    record_event(
        "chat_task_context_assembled",
        repo=repo,
        status="ok",
        data={"task_id": task_id, "classification": classification},
    )
    return ctx


def _build_plan(ctx: ChatTaskContext) -> dict:
    from orchestrator.roles.ingest import build_task_specification
    from orchestrator.roles.planner import build_execution_plan

    task_spec = build_task_specification(
        repo=ctx.repo,
        issue={"title": ctx.message[:200], "body": ctx.message, "number": None},
        constraints=["User-initiated chat task; confirm plan before executing"],
    )
    likely_files = ctx.file_tree[:30]
    plan = build_execution_plan(task_spec, likely_files=likely_files)

    ctx.plan = plan.model_dump()
    ctx.status = "plan_ready"
    _ACTIVE_TASKS[ctx.task_id] = ctx
    record_event(
        "chat_task_plan_ready",
        repo=ctx.repo,
        status="ok",
        data={"task_id": ctx.task_id, "complexity": plan.complexity_score},
    )
    return ctx.plan


def create_chat_task(
    *,
    repo: str,
    message: str,
    history: list[dict] | None = None,
    repo_path: str = "",
) -> ChatTaskContext:
    ctx = _assemble_context(
        repo=repo,
        message=message,
        history=history or [],
        repo_path=repo_path,
    )
    _build_plan(ctx)
    return ctx


def get_chat_task(task_id: str) -> ChatTaskContext | None:
    return _ACTIVE_TASKS.get(task_id)


def list_chat_tasks() -> list[ChatTaskContext]:
    return list(_ACTIVE_TASKS.values())


def cancel_chat_task(task_id: str) -> bool:
    ctx = _ACTIVE_TASKS.get(task_id)
    if not ctx:
        return False
    ctx.status = "cancelled"
    _ACTIVE_TASKS[task_id] = ctx
    record_event("chat_task_cancelled", repo=ctx.repo, status="ok", data={"task_id": task_id})
    return True
