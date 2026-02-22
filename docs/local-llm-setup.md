# Local LLM Setup

Source of truth: `ai-agent/llm/provider.py`

## Provider modes

1. `LLM_PROVIDER=openai`
2. `LLM_PROVIDER=local`
3. `LLM_PROVIDER=auto` (tries providers in `LLM_PROVIDER_ORDER`)

Default: `LLM_PROVIDER=openai`, `LLM_PROVIDER_ORDER=local,openai` (so local is tried first when provider=auto).

## Required env for local mode

```bash
export LLM_PROVIDER=local
export LOCAL_LLM_BASE_URL=http://127.0.0.1:11434
export LOCAL_LLM_API_MODE=chat
export LOCAL_LLM_MODEL=qwen2.5-coder:7b
```

## Current VM status (2026-02-22 UTC)

1. `http://127.0.0.1:11434/v1/models` is currently unreachable (no local model server running).
2. With default fallback enabled, runtime selects OpenAI when local health check fails.
3. If fallback is disabled and `LLM_PROVIDER=local`, agent readiness becomes `false` until local endpoint is healthy.

## Reliability controls

1. `LLM_FALLBACK_ENABLED=true`
2. `LLM_HEALTHCHECK_ENABLED=true`
3. `LLM_REQUIRE_HEALTHY=true`
4. `LLM_HEALTHCHECK_TIMEOUT_SECONDS=2.5`
5. `LLM_HEALTHCHECK_CACHE_SECONDS=45`

## Telemetry

1. `LLM_TELEMETRY_ENABLED=true`
2. `LLM_TELEMETRY_FILE` defaults to `paths.LOGS_DIR/llm_telemetry.jsonl`

Counters are also exposed in runtime state under `state["llm_runtime"]["telemetry_counters"]`.

## Ollama bootstrap

```bash
cd /home/codingai
scripts/setup_local_llm_ollama.sh qwen2.5-coder:7b
```

After bootstrap, validate:

```bash
curl -s http://127.0.0.1:11434/v1/models
```

Strict local-only validation (no fallback):

```bash
cd /tmp/codingai-localstate/ai-agent
PYTHONPATH=. LLM_PROVIDER=local LLM_FALLBACK_ENABLED=false LLM_REQUIRE_HEALTHY=true \
  /home/codingai/ai-agent/venv/bin/python -c "from llm.provider import ensure_llm_ready; print(ensure_llm_ready(force=True))"
```

## Notes

1. Strategy selection and patch generation share the same provider pipeline.
2. `LOCAL_LLM_API_MODE=responses` is supported for strict OpenAI-compatible local gateways.
3. Health/fallback decisions are cached briefly for performance.
