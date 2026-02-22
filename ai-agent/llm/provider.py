import json
import os
import time

import requests
from dotenv import load_dotenv

from paths import ENV_FILE, LOGS_DIR

load_dotenv(str(ENV_FILE))

_TRUTHY = {"1", "true", "yes", "on"}
_VALID_PROVIDERS = {"openai", "local"}

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").strip().lower()  # openai | local | auto
LLM_PROVIDER_ORDER = os.getenv("LLM_PROVIDER_ORDER", "local,openai").strip()
LLM_FALLBACK_ENABLED = os.getenv("LLM_FALLBACK_ENABLED", "true").strip().lower() in _TRUTHY
LLM_HEALTHCHECK_ENABLED = os.getenv("LLM_HEALTHCHECK_ENABLED", "true").strip().lower() in _TRUTHY
LLM_REQUIRE_HEALTHY = os.getenv("LLM_REQUIRE_HEALTHY", "true").strip().lower() in _TRUTHY
LLM_HEALTHCHECK_TIMEOUT_SECONDS = float(os.getenv("LLM_HEALTHCHECK_TIMEOUT_SECONDS", "2.5"))
LLM_HEALTHCHECK_CACHE_SECONDS = int(os.getenv("LLM_HEALTHCHECK_CACHE_SECONDS", "45"))
LLM_TELEMETRY_FILE = os.getenv(
    "LLM_TELEMETRY_FILE",
    str((LOGS_DIR / "llm_telemetry.jsonl").resolve()),
).strip()
LLM_TELEMETRY_ENABLED = os.getenv("LLM_TELEMETRY_ENABLED", "true").strip().lower() in _TRUTHY

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com").strip()

LOCAL_LLM_BASE_URL = os.getenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434").strip()
LOCAL_LLM_API_KEY = os.getenv("LOCAL_LLM_API_KEY", "").strip()
LOCAL_LLM_API_MODE = os.getenv("LOCAL_LLM_API_MODE", "chat").strip().lower()  # chat | responses
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "").strip()
LOCAL_LLM_TEMPERATURE = float(os.getenv("LOCAL_LLM_TEMPERATURE", "0.1"))

_ACTIVE_PROVIDER = None
_LAST_READY_RESULT = None
_LAST_READY_CHECK_AT = 0.0
_TELEMETRY_COUNTERS = {
    "requests_total": 0,
    "requests_success": 0,
    "requests_error": 0,
    "requests_by_provider": {"openai": 0, "local": 0},
    "success_by_provider": {"openai": 0, "local": 0},
    "error_by_provider": {"openai": 0, "local": 0},
    "failovers": 0,
}


def _now_ts() -> int:
    return int(time.time())


def _normalize_provider(name: str) -> str | None:
    n = (name or "").strip().lower()
    if n in _VALID_PROVIDERS:
        return n
    return None


def _requested_provider() -> str:
    if LLM_PROVIDER in {"openai", "local", "auto"}:
        return LLM_PROVIDER
    return "openai"


def _provider_order() -> list[str]:
    parts = [p.strip().lower() for p in LLM_PROVIDER_ORDER.split(",") if p.strip()]
    out = []
    seen = set()
    for p in parts:
        n = _normalize_provider(p)
        if not n or n in seen:
            continue
        out.append(n)
        seen.add(n)
    if not out:
        out = ["local", "openai"]
    return out


def _provider_is_configured(provider: str) -> bool:
    if provider == "openai":
        return bool(OPENAI_API_KEY)
    if provider == "local":
        return bool(LOCAL_LLM_BASE_URL)
    return False


def provider_chain() -> list[str]:
    requested = _requested_provider()
    if requested == "auto":
        return _provider_order()

    chain = [requested]
    if LLM_FALLBACK_ENABLED:
        for p in _provider_order():
            if p != requested and p not in chain:
                chain.append(p)
    return chain


def _with_v1(base_url: str) -> str:
    b = (base_url or "").strip().rstrip("/")
    if not b:
        return ""
    if b.endswith("/v1"):
        return b
    return f"{b}/v1"


def _safe_text(value, max_len=400) -> str:
    text = str(value or "").strip().replace("\n", " ")
    return text[:max_len]


def _telemetry_event(event: str, **fields):
    if not LLM_TELEMETRY_ENABLED:
        return
    entry = {"ts": _now_ts(), "event": event}
    entry.update(fields)
    try:
        parent = os.path.dirname(LLM_TELEMETRY_FILE)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(LLM_TELEMETRY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=True) + "\n")
    except Exception:
        pass


def check_provider_health(provider: str) -> dict:
    provider = _normalize_provider(provider) or provider
    if provider not in _VALID_PROVIDERS:
        return {"provider": provider, "configured": False, "healthy": False, "reason": "invalid_provider"}

    configured = _provider_is_configured(provider)
    if not configured:
        return {"provider": provider, "configured": False, "healthy": False, "reason": "not_configured"}

    if not LLM_HEALTHCHECK_ENABLED:
        return {"provider": provider, "configured": True, "healthy": True, "reason": "healthcheck_disabled"}

    timeout = max(0.2, float(LLM_HEALTHCHECK_TIMEOUT_SECONDS))
    try:
        if provider == "openai":
            url = f"{_with_v1(OPENAI_BASE_URL)}/models"
            headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
            r = requests.get(url, headers=headers, timeout=timeout)
            if r.status_code == 200:
                return {"provider": provider, "configured": True, "healthy": True, "reason": "ok"}
            return {
                "provider": provider,
                "configured": True,
                "healthy": False,
                "reason": "http_error",
                "status_code": r.status_code,
                "details": _safe_text(r.text),
            }

        # local provider: support both OpenAI-compatible and Ollama native probe.
        v1_models = f"{_with_v1(LOCAL_LLM_BASE_URL)}/models"
        r = requests.get(v1_models, timeout=timeout)
        if r.status_code == 200:
            return {"provider": provider, "configured": True, "healthy": True, "reason": "ok_v1_models"}

        tags_url = f"{LOCAL_LLM_BASE_URL.rstrip('/')}/api/tags"
        r2 = requests.get(tags_url, timeout=timeout)
        if r2.status_code == 200:
            return {"provider": provider, "configured": True, "healthy": True, "reason": "ok_ollama_tags"}

        return {
            "provider": provider,
            "configured": True,
            "healthy": False,
            "reason": "http_error",
            "status_code": r2.status_code,
            "details": _safe_text(r2.text),
        }
    except requests.RequestException as e:
        return {
            "provider": provider,
            "configured": True,
            "healthy": False,
            "reason": "request_error",
            "details": _safe_text(e),
        }


def ensure_llm_ready(force: bool = False) -> dict:
    global _ACTIVE_PROVIDER, _LAST_READY_RESULT, _LAST_READY_CHECK_AT

    now = time.time()
    if (
        not force
        and _LAST_READY_RESULT is not None
        and (now - _LAST_READY_CHECK_AT) < max(0, LLM_HEALTHCHECK_CACHE_SECONDS)
    ):
        return _LAST_READY_RESULT

    chain = provider_chain()
    checks = []
    selected = None

    for provider in chain:
        result = check_provider_health(provider)
        checks.append(result)
        if result.get("configured") and result.get("healthy"):
            selected = provider
            break

    if selected is None and not LLM_REQUIRE_HEALTHY:
        for provider in chain:
            if _provider_is_configured(provider):
                selected = provider
                break

    if selected:
        _ACTIVE_PROVIDER = selected

    ready = bool(selected)
    out = {
        "ready": ready,
        "requested_provider": _requested_provider(),
        "active_provider": selected,
        "provider_chain": chain,
        "fallback_enabled": LLM_FALLBACK_ENABLED,
        "require_healthy": LLM_REQUIRE_HEALTHY,
        "healthcheck_enabled": LLM_HEALTHCHECK_ENABLED,
        "checks": checks,
        "checked_at": _now_ts(),
    }

    _LAST_READY_RESULT = out
    _LAST_READY_CHECK_AT = now

    _telemetry_event(
        "llm_provider_selected",
        ready=ready,
        requested_provider=out["requested_provider"],
        active_provider=out["active_provider"],
        chain=chain,
        checks=checks,
    )
    return out


def _effective_provider() -> str:
    if _ACTIVE_PROVIDER in _VALID_PROVIDERS:
        return _ACTIVE_PROVIDER

    requested = _requested_provider()
    if requested in _VALID_PROVIDERS:
        return requested

    for provider in provider_chain():
        if _provider_is_configured(provider):
            return provider
    return "openai"


def provider_name() -> str:
    return _effective_provider()


def llm_is_configured() -> bool:
    return any(_provider_is_configured(provider) for provider in provider_chain())


def resolve_model(default_model: str) -> str:
    provider = _effective_provider()
    if provider == "local" and LOCAL_LLM_MODEL:
        return LOCAL_LLM_MODEL
    return default_model


def try_failover(current_provider: str, reason: str = "") -> str | None:
    global _ACTIVE_PROVIDER

    if not LLM_FALLBACK_ENABLED:
        return None

    chain = provider_chain()
    try:
        idx = chain.index(current_provider)
    except ValueError:
        idx = -1

    for candidate in chain[idx + 1:]:
        health = check_provider_health(candidate)
        if health.get("configured") and (health.get("healthy") or not LLM_REQUIRE_HEALTHY):
            _ACTIVE_PROVIDER = candidate
            _telemetry_event(
                "llm_provider_failover",
                from_provider=current_provider,
                to_provider=candidate,
                reason=_safe_text(reason),
                health=health,
            )
            _TELEMETRY_COUNTERS["failovers"] += 1
            return candidate

    return None


def record_llm_request_result(
    *,
    provider: str,
    operation: str,
    ok: bool,
    status_code: int | None = None,
    retry_count: int = 0,
    latency_ms: int | None = None,
    error: str = "",
):
    provider = _normalize_provider(provider) or provider

    _TELEMETRY_COUNTERS["requests_total"] += 1
    if provider in _TELEMETRY_COUNTERS["requests_by_provider"]:
        _TELEMETRY_COUNTERS["requests_by_provider"][provider] += 1

    if ok:
        _TELEMETRY_COUNTERS["requests_success"] += 1
        if provider in _TELEMETRY_COUNTERS["success_by_provider"]:
            _TELEMETRY_COUNTERS["success_by_provider"][provider] += 1
    else:
        _TELEMETRY_COUNTERS["requests_error"] += 1
        if provider in _TELEMETRY_COUNTERS["error_by_provider"]:
            _TELEMETRY_COUNTERS["error_by_provider"][provider] += 1

    _telemetry_event(
        "llm_request",
        provider=provider,
        operation=operation,
        ok=ok,
        status_code=status_code,
        retry_count=retry_count,
        latency_ms=latency_ms,
        error=_safe_text(error),
    )


def get_llm_runtime_status() -> dict:
    return {
        "requested_provider": _requested_provider(),
        "active_provider": _ACTIVE_PROVIDER,
        "provider_chain": provider_chain(),
        "configured_providers": [p for p in _VALID_PROVIDERS if _provider_is_configured(p)],
        "healthcheck_enabled": LLM_HEALTHCHECK_ENABLED,
        "require_healthy": LLM_REQUIRE_HEALTHY,
        "fallback_enabled": LLM_FALLBACK_ENABLED,
        "telemetry_file": LLM_TELEMETRY_FILE,
        "telemetry_enabled": LLM_TELEMETRY_ENABLED,
        "telemetry_counters": dict(_TELEMETRY_COUNTERS),
        "last_ready": _LAST_READY_RESULT,
    }


def build_llm_request(*, model: str, instructions: str, input_text: str, max_output_tokens: int):
    provider = _effective_provider()
    headers = {"Content-Type": "application/json"}

    if provider == "openai":
        if OPENAI_API_KEY:
            headers["Authorization"] = f"Bearer {OPENAI_API_KEY}"
        url = f"{_with_v1(OPENAI_BASE_URL)}/responses"
        payload = {
            "model": model,
            "instructions": instructions,
            "input": input_text,
            "max_output_tokens": max_output_tokens,
            "text": {"format": {"type": "text"}},
        }
        return provider, url, headers, payload

    # local provider
    if LOCAL_LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LOCAL_LLM_API_KEY}"

    if LOCAL_LLM_API_MODE == "responses":
        url = f"{_with_v1(LOCAL_LLM_BASE_URL)}/responses"
        payload = {
            "model": model,
            "instructions": instructions,
            "input": input_text,
            "max_output_tokens": max_output_tokens,
            "text": {"format": {"type": "text"}},
        }
        return provider, url, headers, payload

    # default: chat/completions (works with Ollama OpenAI-compatible endpoint)
    url = f"{_with_v1(LOCAL_LLM_BASE_URL)}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": input_text},
        ],
        "temperature": LOCAL_LLM_TEMPERATURE,
    }
    if max_output_tokens and max_output_tokens > 0:
        payload["max_tokens"] = int(max_output_tokens)
    return provider, url, headers, payload


def extract_output_text(data: dict) -> str:
    if not isinstance(data, dict):
        return ""

    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        msg = choices[0].get("message", {})
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    txt = item.get("text")
                    if isinstance(txt, str) and txt:
                        parts.append(txt)
            if parts:
                return "\n".join(parts)

    output = data.get("output")
    if isinstance(output, list):
        parts = []
        for entry in output:
            if not isinstance(entry, dict):
                continue
            content = entry.get("content")
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict):
                        txt = c.get("text")
                        if isinstance(txt, str) and txt:
                            parts.append(txt)
        if parts:
            return "\n".join(parts)

    return ""
