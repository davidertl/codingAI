import logging
import os
import time
from dataclasses import dataclass, field

from core.observability import record_event

log = logging.getLogger(__name__)

DOCKER_HOST = os.getenv("DOCKER_HOST", "")
CONTAINER_LABEL = "codingai-worker"
CONTAINER_TTL_SECONDS = int(os.getenv("CONTAINER_TTL_SECONDS", "3600") or "3600")
DEFAULT_TIMEOUT_SECONDS = int(os.getenv("CONTAINER_TIMEOUT_SECONDS", "300") or "300")
DEFAULT_NETWORK_MODE = os.getenv("CONTAINER_NETWORK_MODE", "none").strip()
DEFAULT_MEMORY_LIMIT = os.getenv("CONTAINER_MEMORY_LIMIT", "2g").strip()


@dataclass
class ExecutionResult:
    exit_code: int = 1
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    container_id: str = ""
    duration_ms: int = 0
    base_image: str = ""


_BASE_IMAGE_MAP = {
    "dotnet": "mcr.microsoft.com/dotnet/sdk:8.0",
    "node": "node:20-slim",
    "python": "python:3.13-slim",
    "docker": "docker:27-cli",
    "default": "ubuntu:24.04",
}


def _detect_project_type(repo_analysis: dict) -> str:
    if not isinstance(repo_analysis, dict):
        return "default"
    if repo_analysis.get("dotnet_projects"):
        return "dotnet"
    if repo_analysis.get("node_projects") or repo_analysis.get("web_node_projects"):
        return "node"
    if repo_analysis.get("has_python_files"):
        return "python"
    if repo_analysis.get("has_docker_compose") or repo_analysis.get("has_dockerfile_root"):
        return "docker"
    return "default"


class DockerExecutor:
    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            import docker
        except ImportError:
            raise RuntimeError(
                "Docker SDK not installed. Add 'docker>=7.0.0' to requirements.txt."
            )
        kwargs = {}
        if DOCKER_HOST:
            kwargs["base_url"] = DOCKER_HOST
        self._client = docker.from_env(**kwargs)
        return self._client

    def select_base_image(self, repo_analysis: dict) -> str:
        project_type = _detect_project_type(repo_analysis)
        return _BASE_IMAGE_MAP.get(project_type, _BASE_IMAGE_MAP["default"])

    def run_in_container(
        self,
        *,
        repo_path: str,
        base_image: str,
        commands: list[str],
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        network_mode: str = DEFAULT_NETWORK_MODE,
        memory_limit: str = DEFAULT_MEMORY_LIMIT,
        environment: dict[str, str] | None = None,
        working_dir: str = "/workspace",
    ) -> ExecutionResult:
        client = self._get_client()
        started_at = time.time()
        container = None
        cmd_str = " && ".join(commands) if commands else "echo no-op"

        try:
            container = client.containers.create(
                image=base_image,
                command=["sh", "-c", cmd_str],
                volumes={
                    os.path.abspath(repo_path): {"bind": working_dir, "mode": "rw"},
                },
                working_dir=working_dir,
                network_mode=network_mode,
                mem_limit=memory_limit,
                labels={
                    "managed-by": CONTAINER_LABEL,
                    "created-at": str(int(started_at)),
                },
                environment=environment or {},
                detach=True,
            )
            container.start()

            exit_info = container.wait(timeout=timeout_seconds)
            exit_code = int(exit_info.get("StatusCode", 1))
            timed_out = False
        except Exception as e:
            err_str = str(e).lower()
            if "timed out" in err_str or "read timeout" in err_str:
                timed_out = True
                exit_code = 137
                if container:
                    try:
                        container.kill()
                    except Exception:
                        pass
            else:
                raise
        finally:
            duration_ms = int((time.time() - started_at) * 1000)

        stdout = ""
        stderr = ""
        container_id = ""
        if container:
            container_id = container.short_id
            try:
                logs = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
                stdout = logs[-50_000:] if len(logs) > 50_000 else logs
            except Exception:
                pass
            try:
                err_logs = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
                stderr = err_logs[-20_000:] if len(err_logs) > 20_000 else err_logs
            except Exception:
                pass
            try:
                container.remove(force=True)
            except Exception:
                pass

        record_event(
            "docker_executor_run",
            status="timeout" if timed_out else ("ok" if exit_code == 0 else "failed"),
            data={
                "base_image": base_image,
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "timed_out": timed_out,
                "container_id": container_id,
            },
        )
        return ExecutionResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            container_id=container_id,
            duration_ms=duration_ms,
            base_image=base_image,
        )

    def run_tests_in_container(
        self,
        *,
        repo_path: str,
        repo_name: str,
        repo_analysis: dict,
        strategy_policy: dict | None = None,
    ) -> tuple[bool, str, dict]:
        base_image = self.select_base_image(repo_analysis)
        project_type = _detect_project_type(repo_analysis)

        commands = _build_test_commands(project_type, repo_analysis)
        timeout = int((strategy_policy or {}).get("container_timeout_seconds", DEFAULT_TIMEOUT_SECONDS))

        result = self.run_in_container(
            repo_path=repo_path,
            base_image=base_image,
            commands=commands,
            timeout_seconds=timeout,
        )

        combined_output = result.stdout
        if result.stderr:
            combined_output += f"\n--- STDERR ---\n{result.stderr}"

        test_report = {
            "result": "passed" if result.exit_code == 0 else "failed",
            "strategy_id": f"docker_{project_type}",
            "base_image": result.base_image,
            "container_id": result.container_id,
            "duration_ms": result.duration_ms,
            "timed_out": result.timed_out,
            "attempts": [
                {
                    "attempt": 1,
                    "strategy_id": f"docker_{project_type}",
                    "result": "passed" if result.exit_code == 0 else "failed",
                    "exit_code": result.exit_code,
                }
            ],
        }

        return result.exit_code == 0, combined_output, test_report

    def cleanup_stale_containers(self, max_age_seconds: int = CONTAINER_TTL_SECONDS):
        try:
            client = self._get_client()
        except Exception:
            return 0
        removed = 0
        now = time.time()
        try:
            containers = client.containers.list(
                all=True,
                filters={"label": f"managed-by={CONTAINER_LABEL}"},
            )
            for container in containers:
                created_at = float(container.labels.get("created-at", "0") or "0")
                if created_at and (now - created_at) > max_age_seconds:
                    try:
                        container.remove(force=True)
                        removed += 1
                    except Exception:
                        pass
        except Exception:
            pass
        return removed

    def list_containers(self) -> list[dict]:
        try:
            client = self._get_client()
        except Exception:
            return []
        out = []
        try:
            containers = client.containers.list(
                all=True,
                filters={"label": f"managed-by={CONTAINER_LABEL}"},
            )
            for c in containers:
                out.append({
                    "id": c.short_id,
                    "status": c.status,
                    "image": str(c.image.tags[0]) if c.image.tags else "",
                    "created_at": c.labels.get("created-at", ""),
                })
        except Exception:
            pass
        return out


def _build_test_commands(project_type: str, repo_analysis: dict) -> list[str]:
    if project_type == "node":
        return ["npm ci --ignore-scripts", "npm test"]
    if project_type == "python":
        cmds = ["pip install -r requirements.txt 2>/dev/null || true"]
        if repo_analysis.get("has_pytest"):
            cmds.append("python -m pytest -x --tb=short")
        else:
            cmds.append("python -m unittest discover -s . -p 'test_*.py'")
        return cmds
    if project_type == "dotnet":
        return ["dotnet restore", "dotnet build --no-restore", "dotnet test --no-build"]
    if project_type == "docker":
        if repo_analysis.get("has_docker_compose"):
            return ["docker compose build", "docker compose up --abort-on-container-exit --exit-code-from app"]
        return ["docker build -t codingai-test ."]
    return ["echo 'No test strategy detected'; exit 0"]


def should_use_docker_executor() -> bool:
    mode = os.getenv("RUNNER_MODE", "local_runner").strip().lower()
    return mode == "docker_executor"
