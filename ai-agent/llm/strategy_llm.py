import json
import os
import random
import re
import time

import requests
from dotenv import load_dotenv

load_dotenv("/home/codingai/ai-agent/.env")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
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


def _post_responses_with_backoff(payload: dict):
    """
    Retries on 429 and transient 5xx with exponential backoff + jitter.
    Returns (response, retry_count).
    """
    retry_count = 0
    last_response = None

    for attempt in range(OPENAI_429_MAX_RETRIES + 1):
        try:
            r = requests.post(
                "https://api.openai.com/v1/responses",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=60,
            )
        except requests.RequestException:
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
    if not OPENAI_API_KEY:
        # No key -> deterministic fallback
        return {
            "next_strategy_id": remaining[0]["id"] if remaining else None,
            "reason": "OPENAI_API_KEY not set; fallback to first remaining strategy.",
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

    payload = {
        "model": OPENAI_MODEL,
        "instructions": instructions,
        "input": json.dumps(user, ensure_ascii=False),
        "max_output_tokens": 350,
        "text": {
            "format": {
                "type": "text"
            }
        },
    }

    r, retry_count = _post_responses_with_backoff(payload)
    if r is None:
        return {
            "next_strategy_id": remaining[0]["id"] if remaining else None,
            "reason": "OpenAI API request failed after retries; fallback to first remaining strategy.",
            "confidence": 0.0,
            "retry_count": retry_count,
        }

    if r.status_code >= 400:
        # fallback if API fails
        return {
            "next_strategy_id": remaining[0]["id"] if remaining else None,
            "reason": f"OpenAI API error {r.status_code} after retries; fallback to first remaining strategy.",
            "confidence": 0.0,
            "retry_count": retry_count,
        }

    data = r.json()
    text = data.get("output_text", "")
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
