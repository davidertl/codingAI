import json
import os
import subprocess
import time
from dataclasses import dataclass

from core.observability import record_event
from core.sandbox_runner_client import SandboxRunnerClient, should_use_sandbox_mode
from core.test_runner import run_tests
from orchestrator.contracts import RunOutcome
from orchestrator.errors import AbortRunError, OrchestratorValidationError
from orchestrator.policy import validate_code_bundle
from orchestrator.roles import (
    build_execution_plan,
    build_task_specification,
    generate_code_bundle,
    interpret_test_failure,
    judge_ab_candidates,
    review_code_bundle,
)
from orchestrator.security_checks import validate_success_criteria
from orchestrator.state_store import add_artifact, append_attempt, create_run, finalize_run
from orchestrator.utils import fingerprint_text, sha256_text, utc_now_iso

_TRUTHY = {"1", "true", "yes", "on"}
MAX_IDENTICAL_FAILURES = max(1, int(os.getenv("ORCHESTRATOR_MAX_IDENTICAL_FAILURES", "2") or "2"))
MAX_REPAIR_ATTEMPTS = max(0, int(os.getenv("ORCHESTRATOR_MAX_REPAIR_ATTEMPTS", "2") or "2"))


@dataclass
class OrchestratorResult:
    run_outcome: RunOutcome
    patch_ops: list[dict]
    test_output: str
    test_report: dict
    review_report: dict
    task_spec: dict
    plan: dict
    used_redundancy: bool
    run_id: str


class OrchestratorEngine:
    def __init__(self):
        self.max_identical_failures = MAX_IDENTICAL_FAILURES
        self.max_repair_attempts = MAX_REPAIR_ATTEMPTS
        self.sandbox_runner = SandboxRunnerClient()

    def _run_id(self, repo: str, issue_number: int | None) -> str:
        seed = f"{repo}:{issue_number}:{int(time.time() * 1000)}"
        return f"v2-{fingerprint_text(seed, length=24)}"

    def _git_rev_parse(self, repo_path: str) -> str:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return (result.stdout or "").strip()

    def _git_reset_hard(self, repo_path: str, rev: str):
        subprocess.run(["git", "reset", "--hard", rev], cwd=repo_path, check=True)
        subprocess.run(["git", "clean", "-fd"], cwd=repo_path, check=True)

    def _apply_patch_ops(self, repo_path: str, patch_ops: list[dict]):
        for op in patch_ops:
            if not isinstance(op, dict):
                raise OrchestratorValidationError("patch op must be an object")
            action = str(op.get("action") or "").strip().lower()
            rel_path = str(op.get("path") or "").strip().replace("\\", "/")
            if not rel_path:
                raise OrchestratorValidationError("patch op path missing")
            target_path = os.path.realpath(os.path.join(repo_path, rel_path))
            repo_real = os.path.realpath(repo_path)
            if not (target_path == repo_real or target_path.startswith(repo_real + os.sep)):
                raise OrchestratorValidationError(f"unsafe patch path: {rel_path}")
            if action == "delete":
                if os.path.exists(target_path) and os.path.isfile(target_path):
                    os.remove(target_path)
                continue
            if action not in {"create", "update"}:
                raise OrchestratorValidationError(f"unsupported patch action: {action}")
            parent = os.path.dirname(target_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(target_path, "w", encoding="utf-8", errors="ignore") as f:
                f.write(str(op.get("content") or ""))

    def _repo_has_changes(self, repo_path: str) -> bool:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return bool((result.stdout or "").strip())

    def _execute_tests(
        self,
        *,
        repo_path: str,
        repo_name: str,
        repo_analysis: dict,
        strategy_policy: dict,
    ) -> tuple[bool, str, dict]:
        if should_use_sandbox_mode():
            return self.sandbox_runner.run_tests(
                repo_path=repo_path,
                repo_name=repo_name,
                repo_analysis=repo_analysis,
                strategy_policy=strategy_policy,
            )
        return run_tests(repo_path, repo_name=repo_name)

    def _candidate_summary(self, *, patch_ops: list[dict], review, tests_passed: bool) -> dict:
        review_errors = len([f for f in review.findings if f.level == "error"])
        return {
            "tests_passed": bool(tests_passed),
            "review_errors": review_errors,
            "change_count": len(patch_ops),
            "paths": [str(op.get("path") or "") for op in patch_ops if isinstance(op, dict)],
        }

    def run_issue(
        self,
        *,
        repo_name: str,
        issue: dict,
        repo_path: str,
        repo_analysis: dict,
        strategy_policy: dict | None = None,
        dependency_change_approved: bool = False,
        api_breaking_change_approved: bool = False,
    ) -> OrchestratorResult:
        issue_number = issue.get("number") if isinstance(issue.get("number"), int) else None
        run_id = self._run_id(repo_name, issue_number)
        create_run(run_id=run_id, repo=repo_name, issue_number=issue_number, mode="v2")
        record_event(
            "orchestrator_v2_run_started",
            repo=repo_name,
            issue_number=issue_number,
            status="started",
            data={"run_id": run_id},
        )

        try:
            return self._run_issue_inner(
                run_id=run_id,
                repo_name=repo_name,
                issue=issue,
                repo_path=repo_path,
                repo_analysis=repo_analysis,
                strategy_policy=strategy_policy or {},
                dependency_change_approved=dependency_change_approved,
                api_breaking_change_approved=api_breaking_change_approved,
            )
        except Exception as e:
            summary = f"Orchestrator V2 failed: {e}"
            outcome = RunOutcome(
                run_id=run_id,
                repo=repo_name,
                issue_number=issue_number,
                status="failed",
                summary=summary,
                error_reason=str(e)[:3000],
                success_criteria={},
                artifacts={},
                attempts_count=0,
                ended_at_utc=utc_now_iso(),
            )
            finalize_run(
                run_id=run_id,
                status="failed",
                summary=summary,
                error_reason=str(e),
                success_criteria={},
                artifacts={},
            )
            record_event(
                "orchestrator_v2_run_finished",
                repo=repo_name,
                issue_number=issue_number,
                status="failed",
                data={"run_id": run_id, "error": str(e)[:300]},
            )
            return OrchestratorResult(
                run_outcome=outcome,
                patch_ops=[],
                test_output=str(e),
                test_report={},
                review_report={},
                task_spec={},
                plan={},
                used_redundancy=False,
                run_id=run_id,
            )

    def _run_issue_inner(
        self,
        *,
        run_id: str,
        repo_name: str,
        issue: dict,
        repo_path: str,
        repo_analysis: dict,
        strategy_policy: dict,
        dependency_change_approved: bool,
        api_breaking_change_approved: bool,
    ) -> OrchestratorResult:
        base_rev = self._git_rev_parse(repo_path)

        task_spec = build_task_specification(
            repo=repo_name,
            issue=issue,
            constraints=[
                "Security and determinism override convenience",
                "Do not introduce dependencies unless approved",
                "No secrets in generated content",
                "Complete files only",
            ],
        )
        append_attempt(run_id=run_id, phase="ingest", attempt_index=1, status="ok", payload=task_spec.model_dump())

        likely_files = []
        if isinstance(repo_analysis, dict):
            for path in repo_analysis.get("files", []) or []:
                if isinstance(path, str):
                    likely_files.append(path)
                    if len(likely_files) >= 30:
                        break
        plan = build_execution_plan(task_spec, likely_files=likely_files)
        append_attempt(run_id=run_id, phase="planner", attempt_index=1, status="ok", payload=plan.model_dump())

        add_artifact(run_id=run_id, name="task_spec.json", body=task_spec.model_dump_json(indent=2), sha256=sha256_text(task_spec.model_dump_json()))
        add_artifact(run_id=run_id, name="execution_plan.json", body=plan.model_dump_json(indent=2), sha256=sha256_text(plan.model_dump_json()))

        seen_failure_fingerprints = {}
        best_result = None
        used_redundancy = False
        chosen_patch_ops = []
        chosen_review = None
        chosen_test_output = ""
        chosen_test_report = {}
        attempt_index = 0

        def evaluate_candidate(label: str):
            nonlocal attempt_index
            attempt_index += 1
            self._git_reset_hard(repo_path, base_rev)
            bundle, patch_ops, coder_meta = generate_code_bundle(
                repo_path=repo_path,
                repo_name=repo_name,
                task_spec=task_spec,
                issue=issue,
                repo_analysis=repo_analysis,
                max_ops=max(1, int(strategy_policy.get("max_patch_ops", 20))),
            )
            validate_code_bundle(
                bundle,
                repo_root=repo_path,
                dependency_change_approved=dependency_change_approved,
            )
            review = review_code_bundle(
                task_spec=task_spec,
                bundle=bundle,
                dependency_change_approved=dependency_change_approved,
            )
            if review.has_significant_errors:
                append_attempt(
                    run_id=run_id,
                    phase=f"review_{label}",
                    attempt_index=attempt_index,
                    status="failed",
                    payload=review.model_dump(),
                    fingerprint=fingerprint_text(review.model_dump_json()),
                )
                return {
                    "label": label,
                    "bundle": bundle,
                    "patch_ops": patch_ops,
                    "review": review,
                    "tests_passed": False,
                    "test_output": "review_blocked",
                    "test_report": {},
                    "coder_meta": coder_meta,
                    "review_blocked": True,
                }
            self._apply_patch_ops(repo_path, patch_ops)
            if not self._repo_has_changes(repo_path):
                raise AbortRunError("Generated patch ops produced no effective changes.")
            tests_passed, output, test_report = self._execute_tests(
                repo_path=repo_path,
                repo_name=repo_name,
                repo_analysis=repo_analysis,
                strategy_policy=strategy_policy,
            )
            append_attempt(
                run_id=run_id,
                phase=f"test_{label}",
                attempt_index=attempt_index,
                status="passed" if tests_passed else "failed",
                payload={
                    "test_report": test_report,
                    "test_output_excerpt": str(output or "")[:3000],
                },
                fingerprint=fingerprint_text(output),
            )
            return {
                "label": label,
                "bundle": bundle,
                "patch_ops": patch_ops,
                "review": review,
                "tests_passed": tests_passed,
                "test_output": output,
                "test_report": test_report,
                "coder_meta": coder_meta,
                "review_blocked": False,
            }

        primary = evaluate_candidate("primary")
        best_result = primary

        if not primary["tests_passed"] or primary["review"].has_significant_errors:
            failure_fp = fingerprint_text(str(primary.get("test_output") or primary["review"].model_dump_json()))
            seen_failure_fingerprints[failure_fp] = seen_failure_fingerprints.get(failure_fp, 0) + 1

            for repair_idx in range(1, self.max_repair_attempts + 1):
                if seen_failure_fingerprints.get(failure_fp, 0) >= self.max_identical_failures:
                    raise AbortRunError(
                        f"Repeated failure fingerprint {failure_fp} reached threshold {self.max_identical_failures}."
                    )
                failure_analysis = interpret_test_failure(
                    exit_code=1,
                    logs=str(primary.get("test_output") or ""),
                )
                append_attempt(
                    run_id=run_id,
                    phase="test_interpretation",
                    attempt_index=repair_idx,
                    status="ok",
                    payload=failure_analysis.model_dump(),
                    fingerprint=fingerprint_text(failure_analysis.model_dump_json()),
                )
                candidate = evaluate_candidate(f"repair{repair_idx}")
                if candidate["tests_passed"] and not candidate["review"].has_significant_errors:
                    best_result = candidate
                    break
                best_result = candidate
                failure_fp = fingerprint_text(str(candidate.get("test_output") or candidate["review"].model_dump_json()))
                seen_failure_fingerprints[failure_fp] = seen_failure_fingerprints.get(failure_fp, 0) + 1

        complexity = int(plan.complexity_score)
        needs_redundancy = complexity > 8 and (
            (best_result and not best_result.get("tests_passed"))
            or (best_result and best_result["review"].has_significant_errors)
        )
        if needs_redundancy:
            used_redundancy = True
            cand_a = evaluate_candidate("A")
            cand_b = evaluate_candidate("B")
            decision = judge_ab_candidates(
                self._candidate_summary(
                    patch_ops=cand_a["patch_ops"],
                    review=cand_a["review"],
                    tests_passed=cand_a["tests_passed"],
                ),
                self._candidate_summary(
                    patch_ops=cand_b["patch_ops"],
                    review=cand_b["review"],
                    tests_passed=cand_b["tests_passed"],
                ),
            )
            append_attempt(
                run_id=run_id,
                phase="judge",
                attempt_index=1,
                status="ok",
                payload=decision.model_dump(),
                fingerprint=fingerprint_text(decision.model_dump_json()),
            )
            selected = cand_a if decision.choice in {"A", "MERGE"} else cand_b
            best_result = selected
            if decision.requires_retest:
                self._git_reset_hard(repo_path, base_rev)
                self._apply_patch_ops(repo_path, selected["patch_ops"])
                tests_passed, output, test_report = self._execute_tests(
                    repo_path=repo_path,
                    repo_name=repo_name,
                    repo_analysis=repo_analysis,
                    strategy_policy=strategy_policy,
                )
                selected["tests_passed"] = tests_passed
                selected["test_output"] = output
                selected["test_report"] = test_report
                append_attempt(
                    run_id=run_id,
                    phase="retest_after_judge",
                    attempt_index=1,
                    status="passed" if tests_passed else "failed",
                    payload={"test_report": test_report, "test_output_excerpt": str(output or "")[:3000]},
                    fingerprint=fingerprint_text(output),
                )

        if not best_result:
            raise AbortRunError("No candidate result produced.")

        self._git_reset_hard(repo_path, base_rev)
        self._apply_patch_ops(repo_path, best_result["patch_ops"])
        chosen_patch_ops = best_result["patch_ops"]
        chosen_review = best_result["review"]
        chosen_test_output = str(best_result.get("test_output") or "")
        chosen_test_report = best_result.get("test_report") or {}

        tests_passed = bool(best_result.get("tests_passed"))
        build_passed = tests_passed
        success_criteria = validate_success_criteria(
            task_spec=task_spec,
            bundle=best_result["bundle"],
            review=chosen_review,
            tests_passed=tests_passed,
            build_passed=build_passed,
            dependency_change_approved=dependency_change_approved,
            api_breaking_change_approved=api_breaking_change_approved,
        )
        success = all(bool(v) for v in success_criteria.values())
        status = "success" if success else "failed"
        summary = "V2 orchestration completed successfully." if success else "V2 orchestration failed validation gates."
        error_reason = "" if success else (
            chosen_test_output[:2000]
            if chosen_test_output
            else "One or more success criteria are false."
        )

        artifacts = {
            "run_id": run_id,
            "used_redundancy": used_redundancy,
            "complexity_score": plan.complexity_score,
            "selected_patch_ops_count": len(chosen_patch_ops),
        }
        finalize_run(
            run_id=run_id,
            status=status,
            summary=summary,
            error_reason=error_reason,
            success_criteria=success_criteria,
            artifacts=artifacts,
        )
        add_artifact(
            run_id=run_id,
            name="review_report.json",
            body=chosen_review.model_dump_json(indent=2),
            sha256=sha256_text(chosen_review.model_dump_json()),
        )
        add_artifact(
            run_id=run_id,
            name="test_report.json",
            body=json.dumps(chosen_test_report, ensure_ascii=False, indent=2),
            sha256=sha256_text(json.dumps(chosen_test_report, ensure_ascii=False, sort_keys=True)),
        )

        outcome = RunOutcome(
            run_id=run_id,
            repo=repo_name,
            issue_number=issue.get("number") if isinstance(issue.get("number"), int) else None,
            status=status,
            summary=summary,
            success_criteria=success_criteria,
            error_reason=error_reason,
            artifacts=artifacts,
            attempts_count=attempt_index,
            ended_at_utc=utc_now_iso(),
        )
        record_event(
            "orchestrator_v2_run_finished",
            repo=repo_name,
            issue_number=issue.get("number"),
            status=status,
            data={
                "run_id": run_id,
                "used_redundancy": used_redundancy,
                "attempts": attempt_index,
                "success_criteria": success_criteria,
            },
        )
        return OrchestratorResult(
            run_outcome=outcome,
            patch_ops=chosen_patch_ops,
            test_output=chosen_test_output,
            test_report=chosen_test_report,
            review_report=chosen_review.model_dump(),
            task_spec=task_spec.model_dump(),
            plan=plan.model_dump(),
            used_redundancy=used_redundancy,
            run_id=run_id,
        )
