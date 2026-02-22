# Local LLM Setup

This agent now supports two LLM provider modes:

- `LLM_PROVIDER=openai` (default): uses OpenAI Responses API.
- `LLM_PROVIDER=local`: uses a local OpenAI-compatible endpoint.

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
