import os

from orchestrator.contracts import CodeBundle, ReviewFinding, ReviewReport, TaskSpecification
from orchestrator.policy import _DEPENDENCY_FILES, find_secret_matches


def review_code_bundle(
    *,
    task_spec: TaskSpecification,
    bundle: CodeBundle,
    dependency_change_approved: bool = False,
) -> ReviewReport:
    findings = []
    assertions = {
        "has_changes": bool(bundle.files or bundle.delete_paths),
        "no_dependency_changes": True,
        "no_secret_candidates": True,
        "api_breaking_change_unapproved": True,
        "requirements_addressed": bool(task_spec.requirements),
    }

    if not assertions["has_changes"]:
        findings.append(
            ReviewFinding(
                level="error",
                message="Code bundle is empty.",
                evidence_reasoning="No created/updated/deleted files found.",
            )
        )

    touched_dependency = []
    for artifact in bundle.files:
        if os.path.basename(artifact.path) in _DEPENDENCY_FILES:
            touched_dependency.append(artifact.path)
        secret_hits = find_secret_matches(artifact.content)
        if secret_hits:
            assertions["no_secret_candidates"] = False
            findings.append(
                ReviewFinding(
                    level="error",
                    message="Potential secret-like material detected in generated content.",
                    evidence_file=artifact.path,
                    evidence_reasoning=", ".join(secret_hits[:3]),
                )
            )

    if touched_dependency and not dependency_change_approved:
        assertions["no_dependency_changes"] = False
        findings.append(
            ReviewFinding(
                level="error",
                message="Dependency manifest changes are unapproved.",
                evidence_file=touched_dependency[0],
                evidence_reasoning="Dependency files were modified without explicit approval.",
            )
        )

    api_touch = any(
        path.startswith("ai-agent/service/api.py")
        or path == "ai-agent/service/api.py"
        for path in [f.path for f in bundle.files] + list(bundle.delete_paths)
    )
    if api_touch:
        findings.append(
            ReviewFinding(
                level="warning",
                message="Public API surface touched; compatibility verification required.",
                evidence_file="ai-agent/service/api.py",
                evidence_reasoning="Route or schema changes can break integrations.",
            )
        )

    if not findings:
        findings.append(
            ReviewFinding(
                level="info",
                message="Static review checks passed.",
                evidence_reasoning="No blockers detected in deterministic reviewer checks.",
            )
        )

    return ReviewReport(
        summary="Deterministic static review completed.",
        findings=findings,
        assertions=assertions,
    )
