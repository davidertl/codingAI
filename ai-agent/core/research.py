import hashlib
import json
import os
import time
from typing import Any, Dict, List, Tuple

import requests

from core.observability import record_event
from llm.provider import (
    build_llm_request,
    ensure_llm_ready,
    extract_output_text,
    record_llm_request_result,
    resolve_model,
    try_failover,
)
from llm.rules_instructions import with_rules_instructions
from paths import LOGS_DIR

_TRUTHY = {"1", "true", "yes", "on"}

SEARX_URL = os.getenv("SEARX_URL", "http://127.0.0.1:8080").strip().rstrip("/")
RESEARCH_CACHE_FILE = os.getenv("RESEARCH_CACHE_FILE", str(LOGS_DIR / "research_cache.json")).strip()
RESEARCH_CACHE_TTL_SECONDS = int(os.getenv("RESEARCH_CACHE_TTL_SECONDS", "86400") or "86400")
RESEARCH_MAX_RESULTS = int(os.getenv("RESEARCH_MAX_RESULTS", "6") or "6")
RESEARCH_SUMMARY_MODEL = os.getenv("RESEARCH_SUMMARY_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini")).strip()
RESEARCH_SUMMARY_MAX_TOKENS = int(os.getenv("RESEARCH_SUMMARY_MAX_TOKENS", "320") or "320")
RESEARCH_ENABLE_SUMMARY = os.getenv("RESEARCH_ENABLE_SUMMARY", "true").strip().lower() in _TRUTHY


def _now() -> int:
    return int(time.time())


def _key(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _load_cache() -> dict:
    if not os.path.exists(RESEARCH_CACHE_FILE):
        return {}
    try:
        with open(RESEARCH_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(data: dict):
    try:
        os.makedirs(os.path.dirname(RESEARCH_CACHE_FILE), exist_ok=True)
        with open(RESEARCH_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def _searx_search(query: str, max_results: int) -> Tuple[List[Dict[str, Any]], str]:
    if not SEARX_URL:
        return [], "searx_url_missing"

    try:
        r = requests.get(
            f"{SEARX_URL}/search",
            params={"q": query, "format": "json", "language": "en", "safesearch": 1},
            timeout=10,
        )
    except requests.RequestException as e:
        return [], f"searx_request_error:{e}"

    if r.status_code != 200:
        return [], f"searx_http_{r.status_code}"

    data = r.json()
    items = data.get("results", []) if isinstance(data, dict) else []
    results = []
    for item in items[:max_results]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        url = str(item.get("url", "")).strip()
        content = str(item.get("content", "")).strip()
        if not url:
            continue
        results.append({"title": title or "(untitled)", "url": url, "snippet": content[:400]})
    return results, ""


def _summarize_results(query: str, results: List[Dict[str, str]]) -> Tuple[str, str]:
    if not RESEARCH_ENABLE_SUMMARY or not results:
        return "", ""

    ready = ensure_llm_ready(force=False)
    if not ready.get("ready"):
        return "", "llm_not_ready"

    parts = []
    for idx, r in enumerate(results, start=1):
        parts.append(f"{idx}. {r.get('title')}: {r.get('snippet')} ({r.get('url')})")
    input_text = (
        "Summarize the following search results into bullet points relevant to the query.\n"
        f"Query: {query}\n"
        "Results:\n" + "\n".join(parts)
    )
    model = resolve_model(RESEARCH_SUMMARY_MODEL)
    summary_instructions = with_rules_instructions(
        "Return a concise summary. 4 bullets max. Do not include URLs.",
        global_only=True,
    )
    provider, url, headers, payload = build_llm_request(
        model=model,
        instructions=summary_instructions,
        input_text=input_text,
        max_output_tokens=RESEARCH_SUMMARY_MAX_TOKENS,
    )

    for attempt in range(2):
        started = time.time()
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=30)
            latency_ms = int((time.time() - started) * 1000)
            ok = r.status_code < 400
            record_llm_request_result(
                provider=provider,
                operation="research_summary",
                ok=ok,
                status_code=r.status_code,
                retry_count=attempt,
                latency_ms=latency_ms,
                error="" if ok else r.text[:200],
            )
            if ok:
                summary = extract_output_text(r.json())
                return summary.strip(), ""
        except requests.RequestException as e:
            record_llm_request_result(
                provider=provider,
                operation="research_summary",
                ok=False,
                retry_count=attempt,
                error=str(e),
            )
        failover_to = try_failover(provider, reason="summary_error")
        if not failover_to:
            break
        provider, url, headers, payload = build_llm_request(
            model=model,
            instructions=summary_instructions,
            input_text=input_text,
            max_output_tokens=RESEARCH_SUMMARY_MAX_TOKENS,
        )
    return "", "summary_failed"


def research(query: str, *, max_results: int | None = None, use_cache: bool = True) -> dict:
    q = (query or "").strip()
    if not q:
        raise ValueError("query is required")

    max_results = max(1, min(int(max_results or RESEARCH_MAX_RESULTS), 20))
    now = _now()
    cache = _load_cache()
    cache_key = _key(q.lower())
    cached_entry = cache.get(cache_key) if use_cache else None
    if cached_entry and int(cached_entry.get("expires_at", 0)) > now:
        return cached_entry | {"cached": True}

    results, search_error = _searx_search(q, max_results)
    summary, summary_error = _summarize_results(q, results) if not search_error else ("", "")

    entry = {
        "query": q,
        "results": results,
        "summary": summary,
        "search_error": search_error,
        "summary_error": summary_error,
        "created_at": now,
        "expires_at": now + RESEARCH_CACHE_TTL_SECONDS,
        "cached": False,
    }
    cache[cache_key] = entry
    _save_cache(cache)

    record_event(
        "research_query",
        repo=None,
        status="ok" if not search_error else "error",
        data={"query": q[:160], "results": len(results), "search_error": search_error, "summary_error": summary_error},
    )
    return entry
