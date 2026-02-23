from orchestrator.contracts import TaskSpecification


def _split_lines(text: str, *, max_items: int = 40) -> list[str]:
    lines = []
    for raw in str(text or "").splitlines():
        item = raw.strip().lstrip("-*").strip()
        if not item:
            continue
        lines.append(item)
        if len(lines) >= max_items:
            break
    return lines


def build_task_specification(
    *,
    repo: str,
    issue: dict,
    constraints: list[str] | None = None,
    explicit_acceptance: list[str] | None = None,
) -> TaskSpecification:
    issue_title = str(issue.get("title") or "").strip()
    issue_body = str(issue.get("body") or "").strip()
    objective = issue_title or "Implement requested repository changes"

    requirements = [objective]
    requirements.extend(_split_lines(issue_body, max_items=60))

    acceptance = list(explicit_acceptance or [])
    if not acceptance:
        acceptance = [
            "Build succeeds",
            "Test runner strategies pass",
            "No secrets in modified files",
            "No unapproved dependency changes",
            "No unapproved API breaking changes",
        ]

    risk_level = "medium"
    body_low = issue_body.lower()
    if any(token in body_low for token in ("security", "prod", "critical", "auth", "payment")):
        risk_level = "high"

    return TaskSpecification(
        objective=objective,
        requirements=requirements,
        acceptance_criteria=acceptance,
        affected_modules=[],
        constraints=list(constraints or []),
        risk_level=risk_level,
        ambiguity_notes=[],
        source_issue_number=issue.get("number") if isinstance(issue.get("number"), int) else None,
        source_repo=str(repo or "").strip(),
    )
