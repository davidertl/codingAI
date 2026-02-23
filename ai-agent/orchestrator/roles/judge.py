from orchestrator.contracts import JudgeDecision


def _score_candidate(candidate: dict) -> tuple[int, int, int]:
    passed = 1 if bool(candidate.get("tests_passed")) else 0
    errors = int(candidate.get("review_errors") or 0)
    changes = int(candidate.get("change_count") or 0)
    return passed, -errors, -changes


def judge_ab_candidates(candidate_a: dict, candidate_b: dict) -> JudgeDecision:
    score_a = _score_candidate(candidate_a)
    score_b = _score_candidate(candidate_b)

    if score_a > score_b:
        return JudgeDecision(
            choice="A",
            reason="Candidate A provides better pass/error/change-set tradeoff.",
            requires_retest=not bool(candidate_a.get("tests_passed")),
        )
    if score_b > score_a:
        return JudgeDecision(
            choice="B",
            reason="Candidate B provides better pass/error/change-set tradeoff.",
            requires_retest=not bool(candidate_b.get("tests_passed")),
        )

    files_a = set(candidate_a.get("paths", []) or [])
    files_b = set(candidate_b.get("paths", []) or [])
    overlap = files_a.intersection(files_b)
    if overlap and not (files_a - files_b) and not (files_b - files_a):
        return JudgeDecision(
            choice="MERGE",
            reason="A and B touch identical paths with equivalent score; merge is conflict-free.",
            requires_retest=True,
            merged_paths=sorted(overlap),
        )

    return JudgeDecision(
        choice="A",
        reason="Tie-breaker selects A for deterministic consistency.",
        requires_retest=not bool(candidate_a.get("tests_passed")),
    )
