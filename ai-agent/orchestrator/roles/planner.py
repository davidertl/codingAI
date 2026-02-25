from orchestrator.contracts import ExecutionPlan, ExecutionPlanStep, TaskSpecification


def _estimate_complexity(task_spec: TaskSpecification) -> int:
    score = 2
    score += min(3, len(task_spec.requirements) // 4)
    score += min(2, len(task_spec.acceptance_criteria) // 3)
    score += min(2, len(task_spec.affected_modules))
    score += 2 if task_spec.risk_level in {"high", "critical"} else 0
    return max(0, min(10, score))


def _routing_from_score(score: int) -> str:
    if score < 4:
        return "standard_execution_loop"
    if score <= 7:
        return "repair_aware_loop"
    return "redundancy_mode_ab_with_judge"


def build_execution_plan(task_spec: TaskSpecification, *, likely_files: list[str] | None = None) -> ExecutionPlan:
    complexity = _estimate_complexity(task_spec)
    steps = [
        ExecutionPlanStep(
            step_id="ingest",
            title="Lock task specification",
            details="Normalize objective, requirements, acceptance criteria, and constraints.",
            owner_role="planner",
            expected_output="Validated TaskSpecification",
        ),
        ExecutionPlanStep(
            step_id="codegen",
            title="Generate candidate patch bundle",
            details="Produce full-file code bundle with minimal change surface.",
            owner_role="coder",
            expected_output="CodeBundle",
        ),
        ExecutionPlanStep(
            step_id="review",
            title="Run static review gate",
            details="Evaluate requirement coverage, security, dependency and API risks.",
            owner_role="reviewer",
            expected_output="ReviewReport",
        ),
        ExecutionPlanStep(
            step_id="test",
            title="Execute sandbox test run",
            details="Submit execution profile to test runner and collect sanitized logs.",
            owner_role="test_interpreter",
            expected_output="Pass/fail and structured failure analysis",
        ),
    ]
    if complexity > 7:
        steps.append(
            ExecutionPlanStep(
                step_id="ab",
                title="Redundancy mode",
                details="Generate patch A/B, test each independently, and choose via judge.",
                owner_role="judge",
                expected_output="JudgeDecision with retest",
            )
        )

    hypothesis = ""
    root_cause = ""
    if task_spec.risk_level in {"high", "critical"}:
        hypothesis = f"High-risk change: {task_spec.objective}. Requires careful review before merge."
        root_cause = "Issue reported by user or automated detection."
    elif any(kw in task_spec.objective.lower() for kw in ("bug", "fix", "error", "broken")):
        hypothesis = f"Suspected defect in existing code related to: {task_spec.objective}"
        root_cause = "Likely code defect; root cause to be confirmed by test analysis."

    rollback_strategy = "Revert the branch to base_sha via git reset --hard if tests fail after commit."
    if complexity > 7:
        rollback_strategy = (
            "Staged rollback: revert individual file changes before full branch reset. "
            "Preserve worktree for post-mortem analysis."
        )

    return ExecutionPlan(
        summary=f"Implement objective with deterministic, security-first orchestration: {task_spec.objective}",
        steps=steps,
        likely_affected_files=list(likely_files or task_spec.affected_modules or []),
        test_strategy=[
            "Run deterministic strategy profile in sandbox",
            "If failed, perform repair loop with failure interpretation",
            "If high complexity and significant failure, trigger A/B redundancy",
        ],
        risk_analysis=(
            "High priority checks: secrets, dependency drift, API compatibility, and deterministic full-file outputs."
        ),
        complexity_score=complexity,
        routing_guidance=_routing_from_score(complexity),
        hypothesis=hypothesis,
        root_cause=root_cause,
        rollback_strategy=rollback_strategy,
    )
