# Strategy Mapping

Source: `ai-agent/core/test_runner.py`

## Detection rules

1. `has_docker_compose`: root `docker-compose.yml` exists.
2. `has_dockerfile_root`: root `Dockerfile` exists.
3. `.NET detected`: any `.csproj` or `.sln` outside `.git`/`node_modules`.
4. `Node detected`: any `package.json` outside `.git`/`node_modules`.

## Candidate strategy set and order

1. `docker_compose_build`
2. `docker_compose_ephemeral_up` (only when `DOCKER_COMPOSE_EPHEMERAL_UP_ENABLED=true`)
3. `docker_build`
4. `dotnet_build_docker`
5. `dotnet_build_docker_enable_windows_targeting`
6. `node_build_docker`

## Selection policy

1. First pick:
   - Memory-biased when prior stats exist for remaining strategies.
   - Otherwise deterministic first strategy in candidate order.
2. On failure:
   - LLM proposes next strategy from remaining options only.
   - LLM switch is accepted only if confidence >= `min_confidence_for_switch`.
   - Otherwise fallback uses memory pick, then first remaining.
3. Per-attempt report captures:
   - strategy id/description,
   - pass/fail,
   - error fingerprint.

## Strategy memory shape

Persisted under `state["strategy_memory"][repo][strategy_id]`:

- `runs`
- `successes`
- `failures`
- `last_result`
- `last_error_fingerprint`
- `updated_at`

## Practical constraints

1. `docker_build` only triggers for root `Dockerfile`.
2. Node and .NET flows currently build first discovered project file path.
3. Error text sent to LLM is trimmed to relevant lines to reduce prompt noise.
