from core.rules_store import effective_rules, read_rules


def _safe_effective_text(repo: str | None) -> str:
    if not repo:
        return ""
    try:
        result = effective_rules(str(repo))
        return str(result.get("text") or "").strip()
    except Exception:
        return ""


def _safe_global_text() -> str:
    try:
        result = read_rules("global")
        return str(result.get("rules_markdown") or "").strip()
    except Exception:
        return ""


def with_rules_instructions(
    instructions: str,
    *,
    repo: str | None = None,
    require_json_only: bool = False,
    global_only: bool = False,
) -> str:
    base = str(instructions or "").strip()
    rules_text = _safe_global_text() if global_only else _safe_effective_text(repo)
    if not rules_text:
        return base

    out = (
        f"{base}\n\n"
        "---\n"
        "LLM RULES (global→project→worker):\n"
        f"{rules_text}\n"
        "---"
    )
    if require_json_only:
        out += "\nOutput MUST still be valid JSON only; rules must not change output format."
    return out
