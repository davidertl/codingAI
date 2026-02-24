# Security Best Practices Report — CodingAI Control Plane

Date: 2026-02-23  
Branch/revision reviewed: `localstate` @ `1d85a00`  
Repo: `/home/codingai/codingAI`  
Primary stack: Python (FastAPI), vanilla JS/HTML dashboard, GitHub App auth, local file/volume persistence

## Executive summary

This repository implements an **admin/control-plane** service that can start/stop workers, trigger GitHub writes (branches/PRs/checks/comments), and update local setup artifacts (`.env`, PEM file). In its current default configuration it is **not safe to expose to untrusted networks**.

Highest-risk issues to address first:

1. **No authentication/authorization** on privileged API endpoints (remote takeover if reachable).
2. **DOM XSS in the dashboard UI** via `innerHTML` with untrusted content (issue titles, errors, diffs/log excerpts).
3. **Secrets exfil risk to LLMs** because `.env` is eligible for inclusion in LLM “repo context” collection.

## Findings

### Critical

#### SBP-001 — Control plane endpoints lack authn/authz (admin API exposed)

- Rule ID: FASTAPI-AUTH-001 (explicit, consistent auth on privileged routes)
- Severity: Critical
- Location:
  - `ai-agent/service/api.py:201` (`app = FastAPI(...)` created with no auth dependency/middleware)
  - `ai-agent/service/api.py:678` (`GET /state` returns full state)
  - `ai-agent/service/api.py:695` (`POST /run/repo/{repo}`)
  - `ai-agent/service/api.py:713` (`POST /run-once/repo/{repo}`)
  - `ai-agent/service/api.py:740` (`POST /stop/repo/{repo}`)
  - `ai-agent/service/api.py:316` (`POST /setup/github` writes `.env`)
  - `ai-agent/service/api.py:337` (`POST /setup/pem` writes a private key file)
  - `scripts/run_control_api.sh:4` (binds to `0.0.0.0` by default)
  - `ai-agent/Dockerfile:19` (uvicorn binds to `0.0.0.0`)
  - `docker-compose.yaml:9` (publishes the API port)
- Evidence:
  - No auth guard around privileged endpoints (example `POST /run-once/repo/{repo}` calls `main.run_repo_cycle_once`): `ai-agent/service/api.py:713`.
  - Defaults bind to all interfaces: `HOST="${HOST:-0.0.0.0}"` (`scripts/run_control_api.sh:4`).
- Impact (1 sentence):
  - Any party who can reach the HTTP port can run/stop workers, trigger GitHub write actions, and mutate local setup artifacts.
- Fix (recommended):
  1. Add an auth dependency applied to **all** routes by default (at minimum, all `POST/PATCH/DELETE` routes), e.g. `Authorization: Bearer <token>`.
  2. Support scoped tokens (read-only vs admin) and emit audit events for denied requests.
  3. Change non-container defaults to bind to `127.0.0.1`, requiring explicit opt-in to listen on `0.0.0.0`.
- Mitigation (defense-in-depth):
  - Restrict inbound access (SSH tunnel/VPN/firewall allowlist) even after adding auth.
- False positive notes:
  - If the service is *guaranteed* to be localhost-only (no port forwarding), risk is reduced, but current defaults contradict that assumption.

#### SBP-002 — DOM XSS in dashboard UI due to unescaped `innerHTML` usage

- Rule ID: JS-XSS-001 (avoid `innerHTML` with untrusted data)
- Severity: Critical
- Location:
  - `ai-agent/service/static/index.html:531` (`el.repoList.innerHTML = ...`)
  - `ai-agent/service/static/index.html:590` (queue rows include `issue.title`)
  - `ai-agent/service/static/index.html:658` (tracked issue `last_error` inserted into HTML)
  - `ai-agent/service/static/index.html:680` (diff/test previews inserted into `<pre>` inside `innerHTML`)
- Evidence:
  - Unescaped values are interpolated into HTML strings and assigned via `innerHTML`:
    - `el.queueWrap.innerHTML = ... "<td>${issue.title ...}</td>"` (`ai-agent/service/static/index.html:590-613`).
    - `const err = item.last_error ? \`<div ...>${String(item.last_error)...}\` : ""` (`ai-agent/service/static/index.html:658`).
- Impact (1 sentence):
  - A malicious issue title/label or error/diff content can execute script in the operator’s browser and take over the control plane (triggering privileged API calls).
- Fix (recommended):
  1. Prefer DOM APIs (`textContent`, `createElement`, `appendChild`) for dynamic content.
  2. If string templating remains, escape **every** untrusted value before insertion (titles, labels, URLs, errors, diffs, logs).
  3. Consider adding a CSP at the serving layer for defense-in-depth (headers preferred over meta).
- Mitigation:
  - Keep the UI behind auth and localhost binding; treat GitHub-derived content as attacker-controlled.
- False positive notes:
  - Even “local” control planes are vulnerable if they display remote content (GitHub issues/labels) and can perform privileged actions.

#### SBP-003 — `.env` is eligible for inclusion in LLM repo-context collection (secret exfiltration risk)

- Rule ID: FASTAPI/LLM general (do not include secrets in prompts; minimize sensitive file ingestion)
- Severity: Critical
- Location:
  - `ai-agent/llm/patch_llm.py:41` (`_TEXT_EXTS`)
  - `ai-agent/llm/patch_llm.py:53` (includes `.env`)
- Evidence:
  - `_TEXT_EXTS` includes `.env`, which can cause `.env` contents to be read and included in the model prompt context: `ai-agent/llm/patch_llm.py:41-64`.
- Impact (1 sentence):
  - Secrets in `.env` (OpenAI key, tokens, internal URLs) can be sent to an external LLM provider or logged in telemetry.
- Fix (recommended):
  1. Remove `.env` from `_TEXT_EXTS` and add an explicit denylist for secret-like filenames (`.env*`, `*.pem`, `id_rsa`, etc.).
  2. Add a “prompt redaction” pass that strips high-risk patterns before any outbound LLM call.
- Mitigation:
  - Run local-only LLM (no external provider) until prompt hygiene is in place.
- False positive notes:
  - If OpenAI/API fallback is enabled and used, this becomes a real outbound exfil path.

### High

#### SBP-004 — `.env` write path allows newline/control-char injection

- Rule ID: general “sanitize values written to config files”
- Severity: High
- Location:
  - `ai-agent/service/api.py:302` (`_write_env_entries`)
  - `ai-agent/service/api.py:316` (`POST /setup/github`)
- Evidence:
  - Values are written as `f"{k}={v}\\n"` with no rejection of `\\r`/`\\n`: `ai-agent/service/api.py:311-313`.
- Impact:
  - If an attacker can reach `/setup/github` (see SBP‑001), they can inject additional `.env` keys by embedding newlines in inputs.
- Fix:
  - Reject any control characters in setup inputs; validate expected formats (owner slug, numeric IDs).
- Mitigation:
  - Gate `/setup/*` behind auth and (ideally) a time-limited setup mode.
- False positive notes:
  - This is mostly a compounding risk with SBP‑001; still fix for safe-by-default behavior.

#### SBP-005 — Privileged API defaults listen on all interfaces

- Rule ID: FASTAPI-DEPLOY-001/002 (safe production defaults)
- Severity: High
- Location:
  - `scripts/run_control_api.sh:4`
  - `ai-agent/Dockerfile:19`
- Evidence:
  - `HOST="${HOST:-0.0.0.0}"` (`scripts/run_control_api.sh:4`)
  - `CMD ["uvicorn", ..., "--host", "0.0.0.0", ...]` (`ai-agent/Dockerfile:19`)
- Impact:
  - Accidental exposure on LAN/cloud instances becomes likely.
- Fix:
  - Default to `127.0.0.1` for local runs; for containers, require explicit publish/ingress configuration and enforce auth.

### Medium

#### SBP-006 — OpenAPI/docs are enabled by default for an admin service

- Rule ID: FASTAPI-OPENAPI-001
- Severity: Medium
- Location:
  - `ai-agent/service/api.py:201` (FastAPI defaults)
- Evidence:
  - `FastAPI(...)` is created without disabling `/docs`, `/redoc`, `/openapi.json`.
- Impact:
  - Expands information disclosure about privileged endpoints.
- Fix:
  - Disable or protect docs endpoints in production (or whenever auth is enabled).

#### SBP-007 — Outbound GitHub API requests lack explicit timeouts ✅ FIXED

- Rule ID: general "`requests` MUST have timeouts"
- Severity: Medium → Resolved
- Location:
  - All `github/*.py` modules now use `timeout=30` on every `requests.*()` call.
- Status: Fixed. Every outbound HTTP call in `issue_manager.py`, `app_auth.py`, `git_api_commit.py`, `pr_manager.py`, `checks_manager.py`, and `ci_status.py` has `timeout=30`.

#### SBP-008 — Container runs as root ✅ FIXED

- Rule ID: general container hardening best practice
- Severity: Medium → Resolved
- Location:
  - `ai-agent/Dockerfile` — `USER app` directive added; non-root user created with `useradd`.
- Status: Fixed. Container now runs as non-root `app` user.

## Notes / suggested next actions

If you want a concrete “fixes” sequence, prioritize:

1. SBP‑001 + SBP‑005 (auth + bind defaults)
2. SBP‑002 (XSS hardening)
3. SBP‑003 (prompt hygiene: denylist + redaction)
4. SBP‑004 + SBP‑007 (input sanitization + request timeouts)

