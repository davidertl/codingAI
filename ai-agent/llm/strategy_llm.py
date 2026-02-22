import json
import os
import random
import re
import time

import requests
from dotenv import load_dotenv
from llm.provider import (
    build_llm_request,
    ensure_llm_ready,
    extract_output_text,
    record_llm_request_result,
    llm_is_configured,
    provider_name,
    resolve_model,
    try_failover,
)

from paths import ENV_FILE

load_dotenv(str(ENV_FILE))

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_429_MAX_RETRIES = int(os.getenv("OPENAI_429_MAX_RETRIES", "4"))
OPENAI_429_BASE_BACKOFF_SECONDS = float(os.getenv("OPENAI_429_BASE_BACKOFF_SECONDS", "1.5"))
OPENAI_429_MAX_BACKOFF_SECONDS = float(os.getenv("OPENAI_429_MAX_BACKOFF_SECONDS", "20"))


def _extract_json(text: str) -> dict:
    """
    Robust JSON extraction:
    - find first { ... } block
    - parse json
    """
    if not text:
        raise ValueError("Empty model response")

    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        raise ValueError(f"No JSON object found in model output: {text[:300]}")

    return json.loads(m.group(0))


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    try:
        sec = float(value)
        return max(0.0, sec)
    except Exception:
        return None


def _post_with_backoff(*, model: str, instructions: str, input_text: str, max_output_tokens: int):
    """
    Retries on 429 and transient 5xx with exponential backoff + jitter.
    Returns (response, retry_count).
    """
    retry_count = 0
    last_response = None

    ready = ensure_llm_ready(force=False)
    if not ready.get("ready"):
        return None, retry_count

    for attempt in range(OPENAI_429_MAX_RETRIES + 1):
        try:
            provider, url, headers, payload = build_llm_request(
                model=model,
                instructions=instructions,
                input_text=input_text,
                max_output_tokens=max_output_tokens,
            )
            started = time.time()
            r = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=60,
            )
            latency_ms = int((time.time() - started) * 1000)
        except requests.RequestException as e:
            err_text = str(e) or "request_exception"
            record_llm_request_result(
                provider=provider_name(),
                operation="strategy_pick",
                ok=False,
                retry_count=retry_count,
                error=err_text,
            )
            failover_to = try_failover(provider_name(), reason=err_text)
            if failover_to:
                retry_count += 1
                continue
            if attempt >= OPENAI_429_MAX_RETRIES:
                return None, retry_count

            delay = min(
                OPENAI_429_MAX_BACKOFF_SECONDS,
                OPENAI_429_BASE_BACKOFF_SECONDS * (2 ** attempt),
            )
            delay += random.uniform(0.0, 0.3)
            retry_count += 1
            time.sleep(delay)
            continue

        last_response = r

        if r.status_code == 429:
            record_llm_request_result(
                provider=provider,
                operation="strategy_pick",
                ok=False,
                status_code=429,
                retry_count=retry_count,
                latency_ms=latency_ms,
                error="rate_limited",
            )
            if attempt >= OPENAI_429_MAX_RETRIES:
                return r, retry_count

            retry_after = _parse_retry_after(r.headers.get("Retry-After"))
            if retry_after is None:
                retry_after = min(
                    OPENAI_429_MAX_BACKOFF_SECONDS,
                    OPENAI_429_BASE_BACKOFF_SECONDS * (2 ** attempt),
                )
            retry_after += random.uniform(0.0, 0.3)
            retry_count += 1
            time.sleep(retry_after)
            continue

        if 500 <= r.status_code < 600:
            record_llm_request_result(
                provider=provider,
                operation="strategy_pick",
                ok=False,
                status_code=r.status_code,
                retry_count=retry_count,
                latency_ms=latency_ms,
                error="server_error",
            )
            failover_to = try_failover(provider, reason=f"http_{r.status_code}")
            if failover_to:
                retry_count += 1
                continue
            if attempt >= OPENAI_429_MAX_RETRIES:
                return r, retry_count

            delay = min(
                OPENAI_429_MAX_BACKOFF_SECONDS,
                OPENAI_429_BASE_BACKOFF_SECONDS * (2 ** attempt),
            )
            delay += random.uniform(0.0, 0.3)
            retry_count += 1
            time.sleep(delay)
            continue

        record_llm_request_result(
            provider=provider,
            operation="strategy_pick",
            ok=(r.status_code < 400),
            status_code=r.status_code,
            retry_count=retry_count,
            latency_ms=latency_ms,
            error="" if r.status_code < 400 else "client_error",
        )
        return r, retry_count

    return last_response, retry_count


def pick_next_strategy(
    *,
    repo_name: str,
    repo_analysis: dict,
    last_error: str,
    attempted: list,
    remaining: list,
    strategy_memory: dict | None = None,
) -> dict:
    """
    Ask LLM which strategy to try next.
    Returns dict:
      {
        "next_strategy_id": "...",
        "reason": "...",
        "confidence": 0.0-1.0
      }
    """
    if not llm_is_configured():
        # Provider unavailable -> deterministic fallback
        return {
            "next_strategy_id": remaining[0]["id"] if remaining else None,
            "reason": f"LLM provider '{provider_name()}' is not configured; fallback to first remaining strategy.",
            "confidence": 0.0,
            "retry_count": 0,
        }

    # Keep prompt short & actionable
    instructions = (
        "You are a build/test strategy selector for a CI agent.\n"
        "Pick the next best strategy ID to try based on repository analysis, last error, and strategy memory.\n"
        "Rules:\n"
        "- Output ONLY valid JSON.\n"
        "- Choose next_strategy_id from the provided remaining strategies.\n"
        "- Prefer strategies that address the observed error.\n"
        "- Use strategy_memory as a soft signal only.\n"
        "- Do NOT propose new strategies; only choose from remaining.\n"
        "- Keep reason short (1-3 sentences).\n"
        "JSON schema:\n"
        "{\n"
        '  "next_strategy_id": "string",\n'
        '  "reason": "string",\n'
        '  "confidence": number\n'
        "}\n"
    )

    user = {
        "repo_name": repo_name,
        "repo_analysis": repo_analysis,
        "last_error": last_error[-4000:],  # cap
        "attempted_strategies": attempted,
        "remaining_strategies": remaining,
        "strategy_memory": strategy_memory or {},
    }

    model = resolve_model(OPENAI_MODEL)
    r, retry_count = _post_with_backoff(
        model=model,
        instructions=instructions,
        input_text=json.dumps(user, ensure_ascii=False),
        max_output_tokens=350,
    )
    if r is None:
        return {
            "next_strategy_id": remaining[0]["id"] if remaining else None,
            "reason": f"LLM provider '{provider_name()}' request failed after retries; fallback to first remaining strategy.",
            "confidence": 0.0,
            "retry_count": retry_count,
        }

    if r.status_code >= 400:
        # fallback if API fails
        return {
            "next_strategy_id": remaining[0]["id"] if remaining else None,
            "reason": f"LLM provider '{provider_name()}' API error {r.status_code} after retries; fallback to first remaining strategy.",
            "confidence": 0.0,
            "retry_count": retry_count,
        }

    data = r.json()
    text = extract_output_text(data)
    try:
        out = _extract_json(text)
    except Exception:
        # fallback if malformed output
        return {
            "next_strategy_id": remaining[0]["id"] if remaining else None,
            "reason": "Model output was not valid JSON; fallback to first remaining strategy.",
            "confidence": 0.0,
            "retry_count": retry_count,
        }

    # Validate
    chosen = out.get("next_strategy_id")
    valid_ids = {s["id"] for s in remaining}
    if chosen not in valid_ids:
        return {
            "next_strategy_id": remaining[0]["id"] if remaining else None,
            "reason": "Model chose invalid strategy id; fallback to first remaining strategy.",
            "confidence": 0.0,
            "retry_count": retry_count,
        }

    # Clamp confidence
    try:
        conf = float(out.get("confidence", 0.0))
    except Exception:
        conf = 0.0
    conf = max(0.0, min(1.0, conf))

    return {
        "next_strategy_id": chosen,
        "reason": str(out.get("reason", ""))[:500],
        "confidence": conf,
        "retry_count": retry_count,
    }
