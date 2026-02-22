# Phase 18 – Research (Local-First)
Version: 1.0.0

## Purpose
Local-first retrieval of web context to reduce model calls and cost, with optional LLM summarization and cache.

## Components
- `core/research.py`
  - SearxNG-backed search (`SEARX_URL`, default `http://127.0.0.1:8080`)
  - Cache persisted to `logs/research_cache.json` (configurable via `RESEARCH_CACHE_FILE`)
  - TTL (`RESEARCH_CACHE_TTL_SECONDS`, default 86400s)
  - Max results (`RESEARCH_MAX_RESULTS`, default 6)
  - Optional LLM summary (`RESEARCH_ENABLE_SUMMARY`, default true) using model `RESEARCH_SUMMARY_MODEL` (defaults to `OPENAI_MODEL`) and `RESEARCH_SUMMARY_MAX_TOKENS` (default 320)
- API: `GET /research?query=...`
  - Returns: time_utc, query, cached flag, results[{title,url,snippet}], summary, search_error, summary_error

## Defaults / Env
- `SEARX_URL` – SearxNG endpoint (must be running; no auth assumed).
- `RESEARCH_CACHE_FILE` – path to cache (default logs/research_cache.json).
- `RESEARCH_CACHE_TTL_SECONDS` – cache TTL (default 86400s).
- `RESEARCH_MAX_RESULTS` – cap results (default 6).
- `RESEARCH_ENABLE_SUMMARY` – toggle LLM summarization (default true).
- `RESEARCH_SUMMARY_MODEL` – model id (default OPENAI_MODEL).
- `RESEARCH_SUMMARY_MAX_TOKENS` – summary token cap (default 320).

## Observability
- Events: `research_query` with result count and errors.
- LLM telemetry recorded via `llm/provider.py` for summary calls.

## Usage
- Start SearxNG locally (e.g., `docker run -p 8080:8080 searxng/searxng`).
- Call `GET /research?query=rust async trait` to receive cached/summarized results.

## Notes / Future
- No UI yet (Phase 15/16 unaffected); can add a sidebar research panel later.
- No external API fallback implemented beyond SearxNG; set SEARX_URL to a remote instance if needed.
