# Strategy Mapping

## Current strategy detection logic

From `ai-agent/core/test_runner.py`:

- `has_docker_compose` if `docker-compose.yml` exists at repo root.
- `has_dockerfile_root` if `Dockerfile` exists at repo root.
- `.NET markers`: any `.csproj` or `.sln` anywhere except `.git`/`node_modules`.
- `Node marker`: any `package.json` anywhere except `.git`/`node_modules`.

## Current strategy candidates and order

1. `docker_compose_build`
2. `docker_build`
3. `dotnet_build_docker`
4. `dotnet_build_docker_enable_windows_targeting`
5. `node_build_docker`

First attempt is deterministic (first available by this order). Subsequent attempts are LLM-guided from remaining list.

## Mapping against current workspace repos

### `workspaces/KRT-leadtool`

Detected markers:
- `docker-compose.yml`
- `Dockerfile.backend`, `Dockerfile.frontend` (but no root `Dockerfile`)
- `backend/package.json`
- `frontend/package.json`

Expected candidate list:
1. `docker_compose_build`
2. `node_build_docker`

Notes:
- `docker_build` is not selected because detection expects root `Dockerfile` exactly.

### `workspaces/KRT-leadtool-test`

Detected markers:
- `docker-compose.yml`
- `Dockerfile.backend`, `Dockerfile.frontend` (no root `Dockerfile`)
- `backend/package.json`
- `frontend/package.json`

Expected candidate list:
1. `docker_compose_build`
2. `node_build_docker`

### `workspaces/KRT-Com_Discord`

Detected markers:
- `.sln` and `.csproj`
- no `docker-compose.yml`
- no root `Dockerfile`
- no `package.json` in scanned depth

Expected candidate list:
1. `dotnet_build_docker`
2. `dotnet_build_docker_enable_windows_targeting`

## Edge cases / constraints

- Multi-service Docker repos with non-root Dockerfiles are under-detected for `docker_build`.
- Multi-package Node repos only build first discovered `package.json` path.
- Multi-project .NET repos only build first discovered `.csproj` path.
- No persisted strategy scoring memory; each run starts fresh.
- No confidence threshold enforcement beyond returning clamped confidence value.
