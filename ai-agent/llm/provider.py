import os

from dotenv import load_dotenv

load_dotenv("/home/codingai/ai-agent/.env")

_TRUTHY = {"1", "true", "yes", "on"}

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").strip().lower()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com").strip()

LOCAL_LLM_BASE_URL = os.getenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434").strip()
LOCAL_LLM_API_KEY = os.getenv("LOCAL_LLM_API_KEY", "").strip()
LOCAL_LLM_API_MODE = os.getenv("LOCAL_LLM_API_MODE", "chat").strip().lower()  # chat | responses
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "").strip()
LOCAL_LLM_TEMPERATURE = float(os.getenv("LOCAL_LLM_TEMPERATURE", "0.1"))


def _provider():
    if LLM_PROVIDER in {"openai", "local"}:
        return LLM_PROVIDER
    return "openai"


def provider_name() -> str:
    return _provider()


def _with_v1(base_url: str) -> str:
    b = (base_url or "").strip().rstrip("/")
    if not b:
        return ""
    if b.endswith("/v1"):
        return b
    return f"{b}/v1"


def llm_is_configured() -> bool:
    p = _provider()
    if p == "openai":
        return bool(OPENAI_API_KEY)
    return bool(LOCAL_LLM_BASE_URL)


def resolve_model(default_model: str) -> str:
    p = _provider()
    if p == "local" and LOCAL_LLM_MODEL:
        return LOCAL_LLM_MODEL
    return default_model


def build_llm_request(*, model: str, instructions: str, input_text: str, max_output_tokens: int):
    p = _provider()
    headers = {"Content-Type": "application/json"}

    if p == "openai":
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
        return url, headers, payload

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
        return url, headers, payload

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
    return url, headers, payload


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
