import copy
import json
import os
import time

from paths import AI_AGENT_DIR

_TRUTHY = {"1", "true", "yes", "on"}
_POLICY_CACHE = None
_POLICY_CACHE_AT = 0.0


def _as_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY
    if value is None:
        return default
    return bool(value)


def _as_int(value, default=0, minimum=None, maximum=None):
    try:
        out = int(value)
    except Exception:
        out = int(default)
    if minimum is not None:
        out = max(minimum, out)
    if maximum is not None:
        out = min(maximum, out)
    return out


def _as_float(value, default=0.0, minimum=None, maximum=None):
    try:
        out = float(value)
    except Exception:
        out = float(default)
    if minimum is not None:
        out = max(minimum, out)
    if maximum is not None:
        out = min(maximum, out)
    return out


def _normalize_labels(value):
    if not isinstance(value, list):
        return []
    out = []
    seen = set()
    for item in value:
        label = str(item).strip().lower()
        if not label or label in seen:
            continue
        seen.add(label)
        out.append(label)
    return out


def _normalize_words(value):
    if isinstance(value, str):
        parts = [p.strip().lower() for p in value.split(",") if p.strip()]
        return parts
    if not isinstance(value, list):
        return []
    out = []
    seen = set()
    for item in value:
        word = str(item).strip().lower()
        if not word or word in seen:
            continue
        seen.add(word)
        out.append(word)
    return out


def _deep_merge(base, override):
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _resolve_policy_dir():
    env_dir = os.getenv("POLICY_DIR", "").strip()
    if env_dir:
        return env_dir

    repo_local = os.path.abspath(os.path.join(str(AI_AGENT_DIR), "config", "policies"))
    return repo_local


def _cache_seconds():
    return _as_int(os.getenv("POLICY_CACHE_SECONDS", "20"), default=20, minimum=0, maximum=3600)


def _env_default_policy():
    manual_labels = [
        s.strip().lower()
        for s in os.getenv("MANUAL_APPROVAL_LABELS", "ai-approve,ai-approved,manual-approval-granted").split(",")
        if s.strip()
    ]

    return {
        "enabled": True,
        "dry_run": _as_bool(os.getenv("DRY_RUN", "false"), default=False),
        "safety": {
            "ai_stop_phrase": os.getenv("AI_STOP_PHRASE", "AI Stop").strip() or "AI Stop",
            "failure_cooldown_seconds": _as_int(
                os.getenv("FAIL_COOLDOWN_SECONDS", str(6 * 60 * 60)),
                default=6 * 60 * 60,
                minimum=0,
                maximum=31 * 24 * 60 * 60,
            ),
            "publish_failure_issue": _as_bool(os.getenv("PUBLISH_FAILURE_ISSUE", "true"), default=True),
        },
        "approval": {
            "required": _as_bool(os.getenv("MANUAL_APPROVAL_REQUIRED", "false"), default=False),
            "labels": manual_labels,
            "token": os.getenv("MANUAL_APPROVAL_TOKEN", "[ai-approve]").strip().lower(),
        },
        "pr": {
            "auto_update_enabled": _as_bool(os.getenv("PR_AUTO_UPDATE_ENABLED", "true"), default=True),
            "max_per_day": _as_int(os.getenv("MAX_PRS_PER_REPO_PER_DAY", "3"), default=3, minimum=0, maximum=500),
            "max_per_week": _as_int(
                os.getenv("MAX_PRS_PER_REPO_PER_WEEK", "15"),
                default=15,
                minimum=0,
                maximum=2000,
            ),
            "enable_github_checks": _as_bool(os.getenv("ENABLE_GITHUB_CHECKS", "true"), default=True),
            "check_run_name": os.getenv("CHECK_RUN_NAME", "CodingAI Local Validation").strip() or "CodingAI Local Validation",
        },
        "patch": {
            "max_patch_ops": _as_int(os.getenv("MAX_PATCH_OPS", "20"), default=20, minimum=1, maximum=500),
            "auto_generate_test_patches": _as_bool(os.getenv("AUTO_GENERATE_TEST_PATCHES", "false"), default=False),
            "max_test_patch_ops": _as_int(os.getenv("MAX_TEST_PATCH_OPS", "6"), default=6, minimum=0, maximum=200),
            "max_total_patch_ops": _as_int(os.getenv("MAX_TOTAL_PATCH_OPS", "30"), default=30, minimum=1, maximum=1000),
            "test_patch_on_failure_only": _as_bool(
                os.getenv("TEST_PATCH_ON_FAILURE_ONLY", "true"),
                default=True,
            ),
            "test_patch_min_confidence": _as_float(
                os.getenv("TEST_PATCH_MIN_CONFIDENCE", "0.55"),
                default=0.55,
                minimum=0.0,
                maximum=1.0,
            ),
        },
        "strategy": {
            "switch_confidence_threshold": _as_float(
                os.getenv("STRATEGY_SWITCH_CONFIDENCE_THRESHOLD", "0.65"),
                default=0.65,
                minimum=0.0,
                maximum=1.0,
            ),
            "max_attempts": _as_int(
                os.getenv("STRATEGY_MAX_ATTEMPTS", "3"),
                default=3,
                minimum=1,
                maximum=12,
            ),
            "quarantine_threshold": _as_int(
                os.getenv("STRATEGY_QUARANTINE_THRESHOLD", "3"),
                default=3,
                minimum=1,
                maximum=20,
            ),
            "quarantine_seconds": _as_int(
                os.getenv("STRATEGY_QUARANTINE_SECONDS", str(12 * 60 * 60)),
                default=12 * 60 * 60,
                minimum=0,
                maximum=30 * 24 * 60 * 60,
            ),
            "memory_half_life_seconds": _as_int(
                os.getenv("STRATEGY_MEMORY_HALF_LIFE_SECONDS", str(7 * 24 * 60 * 60)),
                default=7 * 24 * 60 * 60,
                minimum=0,
                maximum=365 * 24 * 60 * 60,
            ),
        },
        "branch": {
            "template": os.getenv("BRANCH_TEMPLATE", "ai/issue-{issue}-iter-{iteration}").strip()
            or "ai/issue-{issue}-iter-{iteration}"
        },
        "ci": {
            "require_green_before_update": _as_bool(
                os.getenv("CI_REQUIRE_GREEN_BEFORE_UPDATE", "false"),
                default=False,
            ),
            "block_on_pending": _as_bool(os.getenv("CI_BLOCK_ON_PENDING", "true"), default=True),
            "block_on_failed": _as_bool(os.getenv("CI_BLOCK_ON_FAILED", "true"), default=True),
            "require_checks_present": _as_bool(os.getenv("CI_REQUIRE_CHECKS_PRESENT", "false"), default=False),
            "allowed_check_conclusions": _normalize_words(
                os.getenv("CI_ALLOWED_CHECK_CONCLUSIONS", "success,neutral,skipped")
            ),
            "on_error": os.getenv("CI_GATE_ON_ERROR", "allow").strip().lower() or "allow",
            "retry_after_seconds": _as_int(
                os.getenv("CI_RETRY_AFTER_SECONDS", "900"),
                default=900,
                minimum=60,
                maximum=7 * 24 * 60 * 60,
            ),
        },
    }


def _normalize_policy(raw_policy):
    merged = _deep_merge(_env_default_policy(), raw_policy or {})

    merged["enabled"] = _as_bool(merged.get("enabled"), default=True)
    merged["dry_run"] = _as_bool(merged.get("dry_run"), default=False)

    safety = merged.setdefault("safety", {})
    safety["ai_stop_phrase"] = str(safety.get("ai_stop_phrase", "AI Stop")).strip() or "AI Stop"
    safety["failure_cooldown_seconds"] = _as_int(
        safety.get("failure_cooldown_seconds", 6 * 60 * 60),
        default=6 * 60 * 60,
        minimum=0,
        maximum=31 * 24 * 60 * 60,
    )
    safety["publish_failure_issue"] = _as_bool(safety.get("publish_failure_issue"), default=True)

    approval = merged.setdefault("approval", {})
    approval["required"] = _as_bool(approval.get("required"), default=False)
    approval["labels"] = _normalize_labels(approval.get("labels", []))
    approval["token"] = str(approval.get("token", "")).strip().lower()

    pr = merged.setdefault("pr", {})
    pr["auto_update_enabled"] = _as_bool(pr.get("auto_update_enabled"), default=True)
    pr["max_per_day"] = _as_int(pr.get("max_per_day", 3), default=3, minimum=0, maximum=500)
    pr["max_per_week"] = _as_int(pr.get("max_per_week", 15), default=15, minimum=0, maximum=2000)
    pr["enable_github_checks"] = _as_bool(pr.get("enable_github_checks"), default=True)
    pr["check_run_name"] = str(pr.get("check_run_name", "CodingAI Local Validation")).strip() or "CodingAI Local Validation"

    patch = merged.setdefault("patch", {})
    patch["max_patch_ops"] = _as_int(patch.get("max_patch_ops", 20), default=20, minimum=1, maximum=500)
    patch["auto_generate_test_patches"] = _as_bool(patch.get("auto_generate_test_patches"), default=False)
    patch["max_test_patch_ops"] = _as_int(patch.get("max_test_patch_ops", 6), default=6, minimum=0, maximum=200)
    patch["max_total_patch_ops"] = _as_int(
        patch.get("max_total_patch_ops", 30),
        default=30,
        minimum=1,
        maximum=1000,
    )
    patch["test_patch_on_failure_only"] = _as_bool(patch.get("test_patch_on_failure_only"), default=True)
    patch["test_patch_min_confidence"] = _as_float(
        patch.get("test_patch_min_confidence", 0.55),
        default=0.55,
        minimum=0.0,
        maximum=1.0,
    )
    if patch["max_total_patch_ops"] < patch["max_patch_ops"]:
        patch["max_total_patch_ops"] = patch["max_patch_ops"]

    strategy = merged.setdefault("strategy", {})
    strategy["switch_confidence_threshold"] = _as_float(
        strategy.get("switch_confidence_threshold", 0.65),
        default=0.65,
        minimum=0.0,
        maximum=1.0,
    )
    strategy["max_attempts"] = _as_int(
        strategy.get("max_attempts", 3),
        default=3,
        minimum=1,
        maximum=12,
    )
    strategy["quarantine_threshold"] = _as_int(
        strategy.get("quarantine_threshold", 3),
        default=3,
        minimum=1,
        maximum=20,
    )
    strategy["quarantine_seconds"] = _as_int(
        strategy.get("quarantine_seconds", 12 * 60 * 60),
        default=12 * 60 * 60,
        minimum=0,
        maximum=30 * 24 * 60 * 60,
    )
    strategy["memory_half_life_seconds"] = _as_int(
        strategy.get("memory_half_life_seconds", 7 * 24 * 60 * 60),
        default=7 * 24 * 60 * 60,
        minimum=0,
        maximum=365 * 24 * 60 * 60,
    )

    branch = merged.setdefault("branch", {})
    template = str(branch.get("template", "ai/issue-{issue}-iter-{iteration}")).strip()
    if not template:
        template = "ai/issue-{issue}-iter-{iteration}"
    branch["template"] = template

    ci = merged.setdefault("ci", {})
    ci["require_green_before_update"] = _as_bool(ci.get("require_green_before_update"), default=False)
    ci["block_on_pending"] = _as_bool(ci.get("block_on_pending"), default=True)
    ci["block_on_failed"] = _as_bool(ci.get("block_on_failed"), default=True)
    ci["require_checks_present"] = _as_bool(ci.get("require_checks_present"), default=False)
    allowed = _normalize_words(ci.get("allowed_check_conclusions", ["success", "neutral", "skipped"]))
    if not allowed:
        allowed = ["success", "neutral", "skipped"]
    ci["allowed_check_conclusions"] = allowed
    on_error = str(ci.get("on_error", "allow")).strip().lower()
    if on_error not in {"allow", "block"}:
        on_error = "allow"
    ci["on_error"] = on_error
    ci["retry_after_seconds"] = _as_int(
        ci.get("retry_after_seconds", 900),
        default=900,
        minimum=60,
        maximum=7 * 24 * 60 * 60,
    )

    return merged


def _load_json_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_policy_store(force=False):
    global _POLICY_CACHE, _POLICY_CACHE_AT

    now = time.time()
    if (
        not force
        and _POLICY_CACHE is not None
        and (now - _POLICY_CACHE_AT) <= _cache_seconds()
    ):
        return _POLICY_CACHE

    policy_dir = _resolve_policy_dir()
    errors = []
    default_override = {}
    repo_overrides = {}

    if os.path.isdir(policy_dir):
        default_path = os.path.join(policy_dir, "default.json")
        if os.path.exists(default_path):
            try:
                parsed = _load_json_file(default_path)
                if isinstance(parsed, dict):
                    default_override = parsed
                else:
                    errors.append(f"default.json is not an object: {default_path}")
            except Exception as e:
                errors.append(f"Failed to parse {default_path}: {e}")

        for name in sorted(os.listdir(policy_dir)):
            if not name.endswith(".json") or name == "default.json":
                continue
            repo = name[:-5]
            path = os.path.join(policy_dir, name)
            try:
                parsed = _load_json_file(path)
                if not isinstance(parsed, dict):
                    errors.append(f"{name} is not an object")
                    continue
                repo_overrides[repo] = parsed
            except Exception as e:
                errors.append(f"Failed to parse {path}: {e}")
    else:
        errors.append(f"Policy directory not found: {policy_dir}")

    default_policy = _normalize_policy(default_override)

    store = {
        "policy_dir": policy_dir,
        "loaded_at": int(time.time()),
        "default_policy": default_policy,
        "repo_overrides": repo_overrides,
        "errors": errors,
    }
    _POLICY_CACHE = store
    _POLICY_CACHE_AT = now
    return store


def get_repo_policy(repo, force=False):
    store = load_policy_store(force=force)
    override = store["repo_overrides"].get(repo, {})
    policy = _normalize_policy(_deep_merge(store["default_policy"], override))
    policy["_meta"] = {
        "repo": repo,
        "policy_dir": store["policy_dir"],
        "loaded_at": store["loaded_at"],
        "has_repo_override": bool(override),
        "errors": list(store["errors"]),
    }
    return policy


def get_policy_snapshot(force=False):
    store = load_policy_store(force=force)
    repos = {}
    for repo in sorted(store["repo_overrides"].keys()):
        repos[repo] = get_repo_policy(repo, force=False)
    return {
        "policy_dir": store["policy_dir"],
        "loaded_at": store["loaded_at"],
        "errors": list(store["errors"]),
        "default_policy": copy.deepcopy(store["default_policy"]),
        "repos": repos,
    }
