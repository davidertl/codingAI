import json
import os
import threading

from core.observability import record_event

_lock = threading.Lock()

ROLE_MODEL_MAP: dict[str, dict] = {}

_KNOWN_ROLES = (
    "classifier",
    "planner",
    "researcher",
    "coder",
    "reviewer",
    "test_interpreter",
    "judge",
)

_ENV_PREFIX = "MODEL_"

_ESCALATION_SUFFIX = "_ESCALATION"


def _load_from_env() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for role in _KNOWN_ROLES:
        env_key = f"{_ENV_PREFIX}{role.upper()}"
        model_val = os.getenv(env_key, "").strip()
        if model_val:
            out[role] = {"model": model_val, "provider": ""}
        esc_key = f"{env_key}{_ESCALATION_SUFFIX}"
        esc_val = os.getenv(esc_key, "").strip()
        if esc_val:
            out.setdefault(role, {})["escalation_model"] = esc_val
    return out


def _load_from_policy(policy: dict | None) -> dict[str, dict]:
    if not isinstance(policy, dict):
        return {}
    section = policy.get("model_routing")
    if not isinstance(section, dict):
        return {}
    out: dict[str, dict] = {}
    for role in _KNOWN_ROLES:
        entry = section.get(role)
        if isinstance(entry, dict):
            out[role] = dict(entry)
        elif isinstance(entry, str) and entry.strip():
            out[role] = {"model": entry.strip(), "provider": ""}
    return out


_runtime_overrides: dict[str, dict] = {}


def set_runtime_routing(routing: dict[str, dict]):
    global _runtime_overrides
    with _lock:
        _runtime_overrides = dict(routing)
    record_event("model_routing_updated", status="ok", data={"roles": list(routing.keys())})


def get_runtime_routing() -> dict[str, dict]:
    with _lock:
        return dict(_runtime_overrides)


def resolve_model_for_role(
    role: str,
    *,
    complexity: str = "medium",
    policy: dict | None = None,
    override_model: str | None = None,
) -> tuple[str, str]:
    """
    Returns (model_name, provider) for a given orchestrator role.
    Resolution order: explicit override > runtime UI override > policy > env var > empty (use default).
    """
    if override_model:
        return override_model.strip(), ""

    with _lock:
        runtime = dict(_runtime_overrides)

    entry = runtime.get(role, {})
    if not entry:
        policy_map = _load_from_policy(policy)
        entry = policy_map.get(role, {})
    if not entry:
        env_map = _load_from_env()
        entry = env_map.get(role, {})

    model = str(entry.get("model") or "").strip()
    provider = str(entry.get("provider") or "").strip()

    if complexity == "high":
        escalation = str(entry.get("escalation_model") or "").strip()
        if escalation:
            model = escalation

    return model, provider


def get_routing_table(*, policy: dict | None = None) -> dict[str, dict]:
    with _lock:
        runtime = dict(_runtime_overrides)
    env_map = _load_from_env()
    policy_map = _load_from_policy(policy)

    table: dict[str, dict] = {}
    for role in _KNOWN_ROLES:
        merged = {}
        if role in env_map:
            merged.update(env_map[role])
        if role in policy_map:
            merged.update(policy_map[role])
        if role in runtime:
            merged.update(runtime[role])
        table[role] = merged if merged else {"model": "", "provider": ""}
    return table
