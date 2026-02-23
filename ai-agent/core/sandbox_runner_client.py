import os
import time

import requests

from orchestrator.errors import ExecutionBoundaryError

_TRUTHY = {"1", "true", "yes", "on"}

RUNNER_MODE = os.getenv("RUNNER_MODE", "local_runner").strip().lower()
SANDBOX_RUNNER_BASE_URL = os.getenv("SANDBOX_RUNNER_BASE_URL", "").strip().rstrip("/")
SANDBOX_RUNNER_TIMEOUT_SECONDS = max(5, int(os.getenv("SANDBOX_RUNNER_TIMEOUT_SECONDS", "300") or "300"))
SANDBOX_RUNNER_POLL_INTERVAL_SECONDS = max(
    1.0,
    float(os.getenv("SANDBOX_RUNNER_POLL_INTERVAL_SECONDS", "2.0") or "2.0"),
)
SANDBOX_RUNNER_VERIFY_TLS = os.getenv("SANDBOX_RUNNER_VERIFY_TLS", "true").strip().lower() in _TRUTHY
SANDBOX_RUNNER_API_KEY = os.getenv("SANDBOX_RUNNER_API_KEY", "").strip()


def _headers() -> dict:
    out = {"Content-Type": "application/json"}
    if SANDBOX_RUNNER_API_KEY:
        out["Authorization"] = f"Bearer {SANDBOX_RUNNER_API_KEY}"
    return out


def _determine_profile(repo_analysis: dict) -> str:
    if not isinstance(repo_analysis, dict):
        return "build_test"
    if repo_analysis.get("web_node_projects") or os.getenv("WEB_SMOKE_BASE_URL", "").strip():
        return "web_playwright"
    if repo_analysis.get("has_python_files") or repo_analysis.get("node_projects") or repo_analysis.get("dotnet_projects"):
        return "build_test"
    if repo_analysis.get("has_docker_compose") or repo_analysis.get("has_dockerfile_root"):
        return "build_test"
    return "static_only"


class SandboxRunnerClient:
    def __init__(self):
        self.base_url = SANDBOX_RUNNER_BASE_URL
        self.timeout_seconds = SANDBOX_RUNNER_TIMEOUT_SECONDS

    def configured(self) -> bool:
        return bool(self.base_url)

    def run_tests(
        self,
        *,
        repo_path: str,
        repo_name: str,
        repo_analysis: dict,
        strategy_policy: dict | None = None,
    ) -> tuple[bool, str, dict]:
        if not self.configured():
            raise ExecutionBoundaryError(
                "RUNNER_MODE=sandbox_api requires SANDBOX_RUNNER_BASE_URL; direct execution is blocked."
            )

        profile = _determine_profile(repo_analysis)
        payload = {
            "repo_name": repo_name,
            "repo_path": repo_path,
            "profile": profile,
            "policy": strategy_policy or {},
        }
        try:
            submit = requests.post(
                f"{self.base_url}/jobs",
                headers=_headers(),
                json=payload,
                timeout=20,
                verify=SANDBOX_RUNNER_VERIFY_TLS,
            )
        except requests.RequestException as e:
            raise ExecutionBoundaryError(f"sandbox job submit failed: {str(e)[:220]}") from e

        if submit.status_code >= 400:
            raise ExecutionBoundaryError(
                f"sandbox job submit http_{submit.status_code}: {(submit.text or '')[:220]}"
            )
        try:
            submit_data = submit.json()
        except ValueError as e:
            raise ExecutionBoundaryError("sandbox submit returned invalid JSON") from e

        job_id = str(submit_data.get("job_id") or "").strip()
        if not job_id:
            raise ExecutionBoundaryError("sandbox submit did not return job_id")

        deadline = time.time() + float(self.timeout_seconds)
        last_payload = {}
        while time.time() < deadline:
            try:
                check = requests.get(
                    f"{self.base_url}/jobs/{job_id}",
                    headers=_headers(),
                    timeout=15,
                    verify=SANDBOX_RUNNER_VERIFY_TLS,
                )
            except requests.RequestException as e:
                raise ExecutionBoundaryError(f"sandbox poll failed: {str(e)[:220]}") from e

            if check.status_code >= 400:
                raise ExecutionBoundaryError(
                    f"sandbox poll http_{check.status_code}: {(check.text or '')[:220]}"
                )
            try:
                data = check.json()
            except ValueError as e:
                raise ExecutionBoundaryError("sandbox poll returned invalid JSON") from e
            last_payload = data
            status = str(data.get("status") or "").strip().lower()
            if status in {"succeeded", "success", "passed"}:
                output = str(data.get("output") or "")
                report = data.get("report") if isinstance(data.get("report"), dict) else {}
                report.setdefault("runner_mode", "sandbox_api")
                report.setdefault("profile", profile)
                report.setdefault("job_id", job_id)
                return True, output, report
            if status in {"failed", "error", "aborted"}:
                output = str(data.get("output") or data.get("error") or "")
                report = data.get("report") if isinstance(data.get("report"), dict) else {}
                report.setdefault("runner_mode", "sandbox_api")
                report.setdefault("profile", profile)
                report.setdefault("job_id", job_id)
                return False, output, report
            time.sleep(SANDBOX_RUNNER_POLL_INTERVAL_SECONDS)

        raise ExecutionBoundaryError(
            f"sandbox job timeout after {self.timeout_seconds}s (job_id={job_id}, last={last_payload})"
        )


def should_use_sandbox_mode() -> bool:
    return RUNNER_MODE == "sandbox_api"
