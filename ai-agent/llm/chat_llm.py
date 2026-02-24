import json
import os
import time

import requests

from llm.provider import (
    build_llm_request,
    build_llm_stream_request,
    ensure_llm_ready,
    extract_output_text,
    iter_llm_chunks,
    record_llm_request_result,
    resolve_model,
    try_failover,
)
from llm.rules_instructions import with_rules_instructions
from llm.prompt_safety import check_prompt_safety

_TASK_TYPES = {"review", "planning", "research", "chat"}

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
CHAT_MODEL_CHAT = os.getenv("CHAT_MODEL_CHAT", OPENAI_MODEL).strip()
CHAT_MODEL_REVIEW = os.getenv("CHAT_MODEL_REVIEW", CHAT_MODEL_CHAT or OPENAI_MODEL).strip()
CHAT_MODEL_PLANNING = os.getenv("CHAT_MODEL_PLANNING", CHAT_MODEL_CHAT or OPENAI_MODEL).strip()
CHAT_MODEL_RESEARCH = os.getenv("CHAT_MODEL_RESEARCH", CHAT_MODEL_CHAT or OPENAI_MODEL).strip()
CHAT_MAX_OUTPUT_TOKENS = int(os.getenv("CHAT_MAX_OUTPUT_TOKENS", "900") or "900")
CHAT_MAX_INPUT_CHARS = int(os.getenv("CHAT_MAX_INPUT_CHARS", "24000") or "24000")
CHAT_LLM_MAX_RETRIES = int(os.getenv("CHAT_LLM_MAX_RETRIES", "2") or "2")


def _normalize_task_type(task_type: str | None) -> str:
    value = str(task_type or "").strip().lower()
    if value in _TASK_TYPES:
        return value
    return "chat"


def route_for_task(task_type: str | None) -> dict:
    normalized = _normalize_task_type(task_type)
    if normalized == "review":
        return {
            "task_type": normalized,
            "model": resolve_model(CHAT_MODEL_REVIEW or OPENAI_MODEL),
            "instructions": (
                "You are a repository review assistant. "
                "Prioritize concrete findings, risks, regressions, and missing tests. "
                "Be direct and concise."
            ),
        }
    if normalized == "planning":
        return {
            "task_type": normalized,
            "model": resolve_model(CHAT_MODEL_PLANNING or OPENAI_MODEL),
            "instructions": (
                "You are a technical planning assistant. "
                "Produce executable, ordered implementation steps with constraints and validation checks."
            ),
        }
    if normalized == "research":
        return {
            "task_type": normalized,
            "model": resolve_model(CHAT_MODEL_RESEARCH or OPENAI_MODEL),
            "instructions": (
                "You are a technical research assistant. "
                "Summarize evidence, cite assumptions explicitly, and separate facts from inference."
            ),
        }
    return {
        "task_type": "chat",
        "model": resolve_model(CHAT_MODEL_CHAT or OPENAI_MODEL),
        "instructions": (
            "You are CodingAI's repository assistant. "
            "Answer concisely, stay grounded in provided repository context, and avoid speculation."
        ),
    }


def _render_attachment(a: dict) -> str:
    path = str(a.get("path", "")).strip()
    start = int(a.get("start_line") or 1)
    end = int(a.get("end_line") or start)
    snippet = str(a.get("snippet") or "")
    return (
        f"[attachment] {path}:{start}-{end}\n"
        "```text\n"
        f"{snippet}\n"
        "```"
    )


def _messages_to_prompt(repo: str, messages: list[dict], *, max_chars: int) -> str:
    lines = [f"Repository: {repo}"]
    total = 0

    for item in messages:
        role = str(item.get("role", "user")).strip().lower() or "user"
        content = str(item.get("content", ""))
        attachments = item.get("attachments", []) if isinstance(item.get("attachments"), list) else []
        block = [f"{role.upper()}:", content]
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            block.append(_render_attachment(attachment))
        block_text = "\n".join(block).strip()
        if not block_text:
            continue
        if total + len(block_text) > max_chars:
            remaining = max(0, max_chars - total)
            if remaining <= 0:
                break
            block_text = block_text[:remaining]
        lines.append(block_text)
        total += len(block_text)
        if total >= max_chars:
            break
    return "\n\n".join(lines).strip()


def chat_completion(*, repo: str, messages: list[dict], task_type: str | None = None) -> dict:
    ready = ensure_llm_ready(force=False)
    route = route_for_task(task_type)
    normalized_task_type = route["task_type"]
    model = route["model"]
    instructions = with_rules_instructions(route["instructions"], repo=repo)

    if not ready.get("ready"):
        return {
            "ok": False,
            "error": "llm_not_ready",
            "text": "",
            "model": model,
            "task_type": normalized_task_type,
            "provider": ready.get("active_provider"),
        }

    input_text = _messages_to_prompt(repo, messages, max_chars=max(2000, CHAT_MAX_INPUT_CHARS))
    if not input_text:
        return {
            "ok": False,
            "error": "empty_prompt",
            "text": "",
            "model": model,
            "task_type": normalized_task_type,
            "provider": ready.get("active_provider"),
        }

    # Prompt safety gate
    _safe, _reason = check_prompt_safety(input_text)
    if not _safe:
        try:
            from core.observability import record_event
            record_event("prompt_safety_block", repo=repo,
                         data={"source": "chat_llm", "reason": _reason})
        except Exception:
            pass
        return {
            "ok": False,
            "error": "prompt_safety_block",
            "text": f"Prompt blocked by safety filter ({_reason}).",
            "model": model,
            "task_type": normalized_task_type,
            "provider": ready.get("active_provider"),
        }

    provider = None
    last_error = ""
    for attempt in range(CHAT_LLM_MAX_RETRIES + 1):
        provider, url, headers, payload = build_llm_request(
            model=model,
            instructions=instructions,
            input_text=input_text,
            max_output_tokens=max(64, CHAT_MAX_OUTPUT_TOKENS),
        )
        started = time.time()
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=90)
        except requests.RequestException as e:
            last_error = str(e)[:320]
            record_llm_request_result(
                provider=provider,
                operation="chat",
                ok=False,
                retry_count=attempt,
                error=last_error,
            )
            failover = try_failover(provider, reason=last_error)
            if failover:
                provider = failover
                continue
            continue

        latency_ms = int((time.time() - started) * 1000)
        if response.status_code >= 400:
            body = (response.text or "")[:220]
            last_error = f"http_{response.status_code}:{body}"
            record_llm_request_result(
                provider=provider,
                operation="chat",
                ok=False,
                status_code=response.status_code,
                retry_count=attempt,
                latency_ms=latency_ms,
                error=last_error,
            )
            failover = try_failover(provider, reason=last_error)
            if failover:
                provider = failover
                continue
            continue

        try:
            data = response.json()
        except json.JSONDecodeError:
            last_error = "invalid_json_response"
            record_llm_request_result(
                provider=provider,
                operation="chat",
                ok=False,
                status_code=response.status_code,
                retry_count=attempt,
                latency_ms=latency_ms,
                error=last_error,
            )
            continue

        text = extract_output_text(data).strip()
        record_llm_request_result(
            provider=provider,
            operation="chat",
            ok=bool(text),
            status_code=response.status_code,
            retry_count=attempt,
            latency_ms=latency_ms,
            error="" if text else "empty_text",
        )
        if text:
            return {
                "ok": True,
                "error": "",
                "text": text,
                "model": model,
                "task_type": normalized_task_type,
                "provider": provider,
            }
        last_error = "empty_text"

    return {
        "ok": False,
        "error": last_error or "chat_failed",
        "text": "",
        "model": model,
        "task_type": normalized_task_type,
        "provider": provider,
    }


def chat_completion_stream(*, repo: str, messages: list[dict], task_type: str | None = None) -> dict:
    """Streaming variant of ``chat_completion``.

    On success returns ``{"ok": True, "response": <requests.Response>, "model": ..., "provider": ...}``.
    The caller should iterate chunks via ``iter_llm_chunks(result["response"])``.
    If the selected endpoint does not support streaming, falls back to the
    non-streaming ``chat_completion`` (result will contain ``"text"`` instead of ``"response"``).
    On error returns ``{"ok": False, "error": ..., ...}`` (same shape as ``chat_completion``).
    """
    ready = ensure_llm_ready(force=False)
    route = route_for_task(task_type)
    normalized_task_type = route["task_type"]
    model = route["model"]
    instructions = with_rules_instructions(route["instructions"], repo=repo)

    _base_err = {
        "model": model,
        "task_type": normalized_task_type,
    }

    if not ready.get("ready"):
        return {**_base_err, "ok": False, "error": "llm_not_ready", "text": "",
                "provider": ready.get("active_provider")}

    input_text = _messages_to_prompt(repo, messages, max_chars=max(2000, CHAT_MAX_INPUT_CHARS))
    if not input_text:
        return {**_base_err, "ok": False, "error": "empty_prompt", "text": "",
                "provider": ready.get("active_provider")}

    _safe, _reason = check_prompt_safety(input_text)
    if not _safe:
        try:
            from core.observability import record_event
            record_event("prompt_safety_block", repo=repo,
                         data={"source": "chat_llm_stream", "reason": _reason})
        except Exception:
            pass
        return {**_base_err, "ok": False, "error": "prompt_safety_block",
                "text": f"Prompt blocked by safety filter ({_reason}).",
                "provider": ready.get("active_provider")}

    provider, url, headers, payload = build_llm_stream_request(
        model=model, instructions=instructions, input_text=input_text,
        max_output_tokens=max(64, CHAT_MAX_OUTPUT_TOKENS),
    )

    if not payload.get("stream"):
        # Endpoint does not support streaming (e.g. /responses) — fall back
        return chat_completion(repo=repo, messages=messages, task_type=task_type)

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=120, stream=True)
    except requests.RequestException as exc:
        last_error = str(exc)[:320]
        record_llm_request_result(provider=provider, operation="chat_stream", ok=False, error=last_error)
        # Try failover to non-streaming
        failover = try_failover(provider, reason=last_error)
        if failover:
            return chat_completion(repo=repo, messages=messages, task_type=task_type)
        return {**_base_err, "ok": False, "error": last_error, "text": "", "provider": provider}

    if response.status_code >= 400:
        body = (response.text or "")[:220]
        last_error = f"http_{response.status_code}:{body}"
        record_llm_request_result(
            provider=provider, operation="chat_stream", ok=False,
            status_code=response.status_code, error=last_error,
        )
        failover = try_failover(provider, reason=last_error)
        if failover:
            return chat_completion(repo=repo, messages=messages, task_type=task_type)
        return {**_base_err, "ok": False, "error": last_error, "text": "", "provider": provider}

    return {
        "ok": True,
        "error": "",
        "response": response,
        "model": model,
        "task_type": normalized_task_type,
        "provider": provider,
    }
