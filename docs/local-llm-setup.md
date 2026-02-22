# Local LLM Setup

This agent supports provider selection and fallback:

- `LLM_PROVIDER=openai` (default): uses OpenAI Responses API.
- `LLM_PROVIDER=local`: uses a local OpenAI-compatible endpoint.
- `LLM_PROVIDER=auto`: tries providers in `LLM_PROVIDER_ORDER` (`local,openai` by default).

## Local mode env variables

Set these before starting `ai-agent/main.py`:

```bash
export LLM_PROVIDER=local
export LOCAL_LLM_BASE_URL=http://127.0.0.1:11434
export LOCAL_LLM_API_MODE=chat
export LOCAL_LLM_MODEL=qwen2.5-coder:7b
```

Optional:

- `LOCAL_LLM_API_KEY` if your local gateway requires auth.
- `LOCAL_LLM_TEMPERATURE` (default `0.1`).
- `LLM_FALLBACK_ENABLED=true` to allow provider failover.
- `LLM_HEALTHCHECK_ENABLED=true` to probe provider readiness.
- `LLM_REQUIRE_HEALTHY=true` to block processing when all providers are unhealthy.
- `LLM_HEALTHCHECK_TIMEOUT_SECONDS=2.5` for probe timeout.
- `LLM_TELEMETRY_ENABLED=true` and `LLM_TELEMETRY_FILE=/home/codingai/ai-agent/logs/llm_telemetry.jsonl`.

## Quick bootstrap with Ollama (Docker)

Use:

```bash
scripts/setup_local_llm_ollama.sh qwen2.5-coder:7b
```

This script:

1. Starts an `ollama/ollama` container.
2. Pulls the requested model.
3. Prints the required environment exports.

## Notes

- Strategy selection and patch generation both use this provider setting.
- For strict OpenAI-compatible Responses endpoints, set `LOCAL_LLM_API_MODE=responses`.
- Runtime provider status and telemetry counters are persisted in `ai-agent/state.json` under `llm_runtime`.
