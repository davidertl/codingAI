import os
import json
import re
import requests
from dotenv import load_dotenv

load_dotenv("/home/codingai/ai-agent/.env")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


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


def pick_next_strategy(*, repo_name: str, repo_analysis: dict, last_error: str, attempted: list, remaining: list) -> dict:
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
        }

    # Keep prompt short & actionable
    instructions = (
        "You are a build/test strategy selector for a CI agent.\n"
        "Pick the next best strategy ID to try based on repository analysis and the last error.\n"
        "Rules:\n"
        "- Output ONLY valid JSON.\n"
        "- Choose next_strategy_id from the provided remaining strategies.\n"
        "- Prefer strategies that address the observed error.\n"
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

    r = requests.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )

    if r.status_code >= 400:
        # fallback if API fails
        return {
            "next_strategy_id": remaining[0]["id"] if remaining else None,
            "reason": f"OpenAI API error {r.status_code}; fallback to first remaining strategy.",
            "confidence": 0.0,
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
        }

    # Validate
    chosen = out.get("next_strategy_id")
    valid_ids = {s["id"] for s in remaining}
    if chosen not in valid_ids:
        return {
            "next_strategy_id": remaining[0]["id"] if remaining else None,
            "reason": "Model chose invalid strategy id; fallback to first remaining strategy.",
            "confidence": 0.0,
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
    }
