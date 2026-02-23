import hashlib
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse

import requests

from llm.strategy_llm import pick_next_strategy

_TRUTHY = {"1", "true", "yes", "on"}
DOCKER_COMPOSE_EPHEMERAL_UP_ENABLED = (
    os.getenv("DOCKER_COMPOSE_EPHEMERAL_UP_ENABLED", "true").strip().lower() in _TRUTHY
)
WEB_SERVER_SCRIPT_CANDIDATES = ("dev", "start", "serve", "preview")
WEB_TEST_SCRIPT_CANDIDATES = ("test:e2e", "e2e", "test:playwright", "playwright", "test:ui")
WEB_FRAMEWORK_DEP_HINTS = (
    "next",
    "nuxt",
    "react",
    "react-dom",
    "vite",
    "@vitejs/plugin-react",
    "astro",
    "svelte",
    "vue",
    "@angular/core",
)
PLAYWRIGHT_CONFIG_NAMES = (
    "playwright.config.ts",
    "playwright.config.js",
    "playwright.config.mjs",
    "playwright.config.cjs",
)
SEMGREP_DOCKER_IMAGE = os.getenv("SEMGREP_DOCKER_IMAGE", "returntocorp/semgrep:latest").strip()
TRIVY_DOCKER_IMAGE = os.getenv("TRIVY_DOCKER_IMAGE", "aquasec/trivy:latest").strip()
SONAR_SCANNER_DOCKER_IMAGE = os.getenv("SONAR_SCANNER_DOCKER_IMAGE", "sonarsource/sonar-scanner-cli:latest").strip()
DEFAULT_SECURITY_SCAN_EXCLUDES = (
    ".git",
    ".playwright",
    ".playwright-cli",
    "output",
    "workspaces",
)
BANDIT_DEFAULT_EXCLUDES = (
    ".git",
    ".playwright",
    ".playwright-cli",
    "output",
    "workspaces",
    ".venv",
    "venv",
    "venv_user",
    "site-packages",
    "ai-agent/venv",
    "ai-agent/venv_user",
    "*/site-packages/*",
    "*/venv/*",
    "*/venv_user/*",
)


def _run(cmd, *, cwd=None, env=None):
    try:
        return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    except FileNotFoundError as e:
        executable = ""
        if isinstance(cmd, (list, tuple)) and cmd:
            executable = str(cmd[0])
        elif isinstance(cmd, str):
            executable = cmd.split()[0] if cmd.strip() else ""
        missing = executable or "command"
        return subprocess.CompletedProcess(
            cmd,
            127,
            stdout="",
            stderr=f"{missing} not found: {e}",
        )


def _command_exists(name: str) -> bool:
    return bool(shutil.which(name))


def _docker_available() -> bool:
    return _run(["docker", "version"]).returncode == 0


def _run_and_combine(cmd, *, cwd=None, env=None):
    res = _run(cmd, cwd=cwd, env=env)
    out = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
    return res.returncode == 0, out


def _csv_list(value: str | None, fallback: tuple[str, ...] = ()) -> list[str]:
    source = str(value or "").strip()
    if not source:
        return [item for item in fallback if str(item).strip()]
    return [item.strip() for item in source.split(",") if item.strip()]


def _git_tracked_python_files(repo_path: str) -> list[str]:
    res = _run(["git", "ls-files", "*.py"], cwd=repo_path)
    if res.returncode != 0:
        return []
    files = []
    for line in (res.stdout or "").splitlines():
        rel = line.strip()
        if not rel:
            continue
        abs_path = os.path.join(repo_path, rel)
        if os.path.isfile(abs_path):
            files.append(rel)
    return files


def _sonarqube_configured() -> bool:
    return bool(os.getenv("SONAR_HOST_URL", "").strip() and os.getenv("SONAR_TOKEN", "").strip())


def _compose_base_cmd() -> list[str]:
    """
    Prefer Docker Compose plugin, fallback to standalone docker-compose binary.
    """
    if _run(["docker", "compose", "version"]).returncode == 0:
        return ["docker", "compose"]
    if _run(["docker-compose", "version"]).returncode == 0:
        return ["docker-compose"]
    # Keep default behavior to surface a clear runtime error.
    return ["docker", "compose"]


def _load_package_json(pkg_path: str) -> dict | None:
    try:
        with open(pkg_path, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return None


def _package_dep_names(package_json: dict) -> set[str]:
    deps = package_json.get("dependencies", {}) if isinstance(package_json, dict) else {}
    dev_deps = package_json.get("devDependencies", {}) if isinstance(package_json, dict) else {}

    names = set()
    if isinstance(deps, dict):
        names.update(str(k).strip() for k in deps.keys() if str(k).strip())
    if isinstance(dev_deps, dict):
        names.update(str(k).strip() for k in dev_deps.keys() if str(k).strip())
    return names


def _web_detection_score(package_json: dict, project_dir: str) -> int:
    scripts = package_json.get("scripts", {}) if isinstance(package_json, dict) else {}
    dep_names = _package_dep_names(package_json)

    score = 0
    if isinstance(scripts, dict):
        script_names = {str(k).strip() for k in scripts.keys() if str(k).strip()}
        if script_names.intersection(WEB_SERVER_SCRIPT_CANDIDATES):
            score += 3
        if script_names.intersection(WEB_TEST_SCRIPT_CANDIDATES):
            score += 2
        if "build" in script_names:
            score += 1

    if dep_names.intersection(WEB_FRAMEWORK_DEP_HINTS):
        score += 2
    if "@playwright/test" in dep_names or "playwright" in dep_names:
        score += 2
    if any(os.path.exists(os.path.join(project_dir, n)) for n in PLAYWRIGHT_CONFIG_NAMES):
        score += 2
    if os.path.exists(os.path.join(project_dir, "index.html")):
        score += 1
    return score


def _pick_script(scripts: dict, candidates: tuple[str, ...]) -> str | None:
    if not isinstance(scripts, dict):
        return None
    for name in candidates:
        val = scripts.get(name)
        if isinstance(val, str) and val.strip():
            return name
    return None


def _pick_free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _tail_text_file(path: str, max_chars: int = 5000) -> str:
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        return content[-max_chars:]
    except Exception:
        return ""


def _terminate_process(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _fingerprint(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8", errors="ignore")).hexdigest()[:12]


def _validate_http_url(url: str) -> str:
    target = str(url or "").strip()
    parsed = urlparse(target)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported URL scheme for test_runner HTTP call: {parsed.scheme or '(missing)'}")
    if not parsed.netloc:
        raise ValueError("HTTP URL is missing host component.")
    return target


def _extract_relevant_error_lines(text: str, max_lines: int = 80, max_chars: int = 4000) -> str:
    """
    Extract high-signal error lines and nearby context to keep logs compact for LLM/state/comments.
    """
    raw = (text or "").strip()
    if not raw:
        return ""

    lines = raw.splitlines()
    keywords = (
        "error",
        "exception",
        "traceback",
        "fatal",
        "failed",
        "cannot",
        "unable",
        "npm err",
        "netsdk",
        "msbuild",
    )

    idx = set()
    for i, line in enumerate(lines):
        low = line.lower()
        if any(k in low for k in keywords):
            idx.add(i)
            if i - 1 >= 0:
                idx.add(i - 1)
            if i + 1 < len(lines):
                idx.add(i + 1)

    if not idx:
        trimmed = "\n".join(lines[-min(50, len(lines)):])
        return trimmed[:max_chars]

    selected = [lines[i] for i in sorted(idx)]
    if len(selected) > max_lines:
        selected = selected[:max_lines]

    out = "\n".join(selected)
    if len(out) > max_chars:
        out = out[:max_chars]
    return out


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def _safe_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return int(default)


def _normalize_strategy_memory(strategy_memory: dict | None) -> dict:
    if not isinstance(strategy_memory, dict):
        return {}

    normalized = {}
    for sid, stats in strategy_memory.items():
        if not isinstance(stats, dict):
            continue
        normalized[sid] = {
            "runs": int(stats.get("runs", 0)),
            "successes": int(stats.get("successes", 0)),
            "failures": int(stats.get("failures", 0)),
            "last_result": str(stats.get("last_result", "")),
            "last_error_fingerprint": str(stats.get("last_error_fingerprint", "")),
            "updated_at": int(stats.get("updated_at", 0)),
            "consecutive_failures": int(stats.get("consecutive_failures", 0)),
            "cooldown_until": int(stats.get("cooldown_until", 0)),
            "last_success_at": int(stats.get("last_success_at", 0)),
            "last_failure_at": int(stats.get("last_failure_at", 0)),
        }
    return normalized


def _strategy_memory_score(strategy_id: str, memory: dict, *, now_ts: int, half_life_seconds: int) -> float:
    stats = memory.get(strategy_id, {})
    runs = int(stats.get("runs", 0))
    successes = int(stats.get("successes", 0))
    consecutive_failures = int(stats.get("consecutive_failures", 0))
    updated_at = int(stats.get("updated_at", 0))

    # Laplace-smoothed success rate, slightly favor proven history.
    success_rate = (successes + 1.0) / (runs + 2.0)
    experience_bonus = min(runs, 20) * 0.01

    decay = 1.0
    if half_life_seconds > 0 and updated_at > 0:
        age = max(0, now_ts - updated_at)
        decay = 0.5 ** (age / float(max(1, half_life_seconds)))

    failure_penalty = min(consecutive_failures, 6) * 0.08
    return (success_rate * decay) + experience_bonus - failure_penalty


def _is_quarantined(strategy_id: str, memory: dict, now_ts: int) -> bool:
    stats = memory.get(strategy_id, {})
    cooldown_until = int(stats.get("cooldown_until", 0))
    return cooldown_until > now_ts


def _pick_by_memory(remaining: list, memory: dict, *, now_ts: int, half_life_seconds: int):
    with_history = []
    for i, s in enumerate(remaining):
        sid = s["id"]
        if _is_quarantined(sid, memory, now_ts):
            continue
        runs = int(memory.get(sid, {}).get("runs", 0))
        if runs <= 0:
            continue
        score = _strategy_memory_score(sid, memory, now_ts=now_ts, half_life_seconds=half_life_seconds)
        with_history.append((score, -i, sid))

    if not with_history:
        return None, None

    with_history.sort(reverse=True)
    best_sid = with_history[0][2]
    best_score = with_history[0][0]

    idx = next((i for i, s in enumerate(remaining) if s["id"] == best_sid), None)
    if idx is None:
        return None, None

    return remaining.pop(idx), best_score


def _update_strategy_memory(
    memory: dict,
    strategy_id: str,
    ok: bool,
    error_text: str,
    *,
    quarantine_threshold: int,
    quarantine_seconds: int,
):
    now_ts = int(time.time())
    stats = memory.setdefault(
        strategy_id,
        {
            "runs": 0,
            "successes": 0,
            "failures": 0,
            "last_result": "",
            "last_error_fingerprint": "",
            "updated_at": 0,
            "consecutive_failures": 0,
            "cooldown_until": 0,
            "last_success_at": 0,
            "last_failure_at": 0,
        },
    )

    stats["runs"] += 1
    if ok:
        stats["successes"] += 1
        stats["last_result"] = "passed"
        stats["last_error_fingerprint"] = ""
        stats["consecutive_failures"] = 0
        stats["cooldown_until"] = 0
        stats["last_success_at"] = now_ts
    else:
        stats["failures"] += 1
        stats["last_result"] = "failed"
        stats["last_error_fingerprint"] = _fingerprint(error_text)
        stats["consecutive_failures"] = int(stats.get("consecutive_failures", 0)) + 1
        stats["last_failure_at"] = now_ts
        if (
            quarantine_threshold > 0
            and quarantine_seconds > 0
            and int(stats.get("consecutive_failures", 0)) >= int(quarantine_threshold)
        ):
            stats["cooldown_until"] = max(int(stats.get("cooldown_until", 0)), now_ts + int(quarantine_seconds))
    stats["updated_at"] = now_ts


def analyze_repo(repo_path: str) -> dict:
    """
    Local repo scan only (no guessing).
    Returns a compact summary for the LLM.
    """
    summary = {
        "repo_path": repo_path,
        "has_docker_compose": os.path.exists(os.path.join(repo_path, "docker-compose.yml")),
        "has_dockerfile_root": os.path.exists(os.path.join(repo_path, "Dockerfile")),
        "dotnet_projects": [],
        "node_projects": [],
        "web_node_projects": [],
        "has_python_files": False,
    }

    for root, _, files in os.walk(repo_path):
        # keep it light
        if "/.git/" in root.replace("\\", "/"):
            continue
        if "node_modules" in root:
            continue

        for f in files:
            if f.endswith(".csproj") or f.endswith(".sln"):
                summary["dotnet_projects"].append(os.path.join(root, f))
            elif f == "package.json":
                pkg_path = os.path.join(root, f)
                summary["node_projects"].append(pkg_path)
                pkg = _load_package_json(pkg_path)
                if pkg and _web_detection_score(pkg, root) > 0:
                    summary["web_node_projects"].append(pkg_path)
            elif f.endswith(".py"):
                summary["has_python_files"] = True

    # cap sizes
    summary["dotnet_projects"] = summary["dotnet_projects"][:20]
    summary["node_projects"] = summary["node_projects"][:20]
    summary["web_node_projects"] = summary["web_node_projects"][:20]
    return summary


def detect_dotnet_sdk_version(csproj_path: str) -> str:
    """
    Minimal detection from file content (no assumptions).
    """
    try:
        with open(csproj_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        if "net10.0" in content:
            return "10.0"
        if "net9.0" in content:
            return "9.0"
        if "net8.0" in content:
            return "8.0"
    except Exception:
        pass
    return "8.0"


def find_first_csproj(repo_analysis: dict) -> str | None:
    for p in repo_analysis.get("dotnet_projects", []):
        if p.endswith(".csproj"):
            return p
    return None


def find_first_package_json(repo_analysis: dict) -> str | None:
    for p in repo_analysis.get("node_projects", []):
        if p.endswith("package.json"):
            return p
    return None


def find_first_web_package(repo_analysis: dict) -> str | None:
    web_candidates = repo_analysis.get("web_node_projects", [])
    if web_candidates:
        return web_candidates[0]

    best_score = -1
    best_path = None
    for p in repo_analysis.get("node_projects", []):
        if not p.endswith("package.json"):
            continue
        pkg = _load_package_json(p)
        if not pkg:
            continue
        score = _web_detection_score(pkg, os.path.dirname(p))
        if score > best_score:
            best_score = score
            best_path = p

    if best_score <= 0:
        return None
    return best_path


# ----------------------------
# Strategies
# ----------------------------

def strat_docker_compose_build(repo_path: str):
    res = _run(_compose_base_cmd() + ["build"], cwd=repo_path)
    out = (res.stdout or "") + "\n" + (res.stderr or "")
    return res.returncode == 0, out.strip()


def _compose_first_service(repo_path: str) -> str | None:
    res = _run(_compose_base_cmd() + ["config", "--services"], cwd=repo_path)
    if res.returncode != 0:
        return None
    for line in (res.stdout or "").splitlines():
        service = line.strip()
        if service:
            return service
    return None


def _compose_project_name(repo_path: str) -> str:
    base = os.path.basename(os.path.abspath(repo_path)).lower()
    safe = "".join(ch for ch in base if ch.isalnum()) or "repo"
    stamp = int(time.time()) % 1000000
    return f"ai{safe[:20]}{stamp}{_fingerprint(repo_path)[:6]}"


def strat_docker_compose_ephemeral_up(repo_path: str):
    service = _compose_first_service(repo_path)
    if not service:
        return False, "Could not resolve docker compose service list for ephemeral run."

    project_name = _compose_project_name(repo_path)
    compose_base = _compose_base_cmd()
    up_cmd = compose_base + [
        "-p", project_name,
        "up", "--build", "--abort-on-container-exit", "--exit-code-from", service,
    ]
    down_cmd = compose_base + [
        "-p", project_name,
        "down", "-v", "--remove-orphans",
    ]

    up = _run(up_cmd, cwd=repo_path)
    down = _run(down_cmd, cwd=repo_path)

    out = []
    out.append(f"ephemeral_project={project_name}")
    out.append("--- docker compose up ---")
    out.append((up.stdout or "").strip())
    out.append((up.stderr or "").strip())
    out.append("--- docker compose down ---")
    out.append((down.stdout or "").strip())
    out.append((down.stderr or "").strip())
    combined = "\n".join(part for part in out if part)
    return up.returncode == 0, combined.strip()


def strat_docker_build(repo_path: str):
    res = _run(["docker", "build", "-t", "ai-test-image", "."], cwd=repo_path)
    out = (res.stdout or "") + "\n" + (res.stderr or "")
    return res.returncode == 0, out.strip()


def strat_dotnet_build_docker(repo_path: str, repo_analysis: dict):
    csproj = find_first_csproj(repo_analysis)
    if not csproj:
        return False, "No .csproj found for dotnet build strategy."

    sdk = detect_dotnet_sdk_version(csproj)
    project_dir = os.path.dirname(csproj)
    rel = os.path.relpath(project_dir, repo_path)

    res = _run(
        [
            "docker", "run", "--rm",
            "-v", f"{repo_path}:/app",
            "-w", f"/app/{rel}",
            f"mcr.microsoft.com/dotnet/sdk:{sdk}",
            "dotnet", "build",
        ]
    )
    out = (res.stdout or "") + "\n" + (res.stderr or "")
    return res.returncode == 0, out.strip()


def strat_dotnet_build_docker_enable_windows_targeting(repo_path: str, repo_analysis: dict):
    """
    Retry strategy for NETSDK1100 (Windows targeting from non-Windows OS).
    Best practice: pass property EnableWindowsTargeting=true.
    """
    csproj = find_first_csproj(repo_analysis)
    if not csproj:
        return False, "No .csproj found for dotnet build strategy."

    sdk = detect_dotnet_sdk_version(csproj)
    project_dir = os.path.dirname(csproj)
    rel = os.path.relpath(project_dir, repo_path)

    res = _run(
        [
            "docker", "run", "--rm",
            "-v", f"{repo_path}:/app",
            "-w", f"/app/{rel}",
            f"mcr.microsoft.com/dotnet/sdk:{sdk}",
            "dotnet", "build", "-p:EnableWindowsTargeting=true",
        ]
    )
    out = (res.stdout or "") + "\n" + (res.stderr or "")
    return res.returncode == 0, out.strip()


def strat_node_build_docker(repo_path: str, repo_analysis: dict):
    pkg = find_first_package_json(repo_analysis)
    if not pkg:
        return False, "No package.json found for node build strategy."

    project_dir = os.path.dirname(pkg)
    rel = os.path.relpath(project_dir, repo_path)

    res = _run(
        [
            "docker", "run", "--rm",
            "-v", f"{repo_path}:/app",
            "-w", f"/app/{rel}",
            "node:20",
            "bash", "-c",
            "npm ci || npm install; npm run build",
        ]
    )
    out = (res.stdout or "") + "\n" + (res.stderr or "")
    return res.returncode == 0, out.strip()


def strat_semgrep_scan(repo_path: str):
    config = os.getenv("SEMGREP_CONFIG", "p/ci").strip() or "p/ci"
    metrics_mode = os.getenv("SEMGREP_METRICS", "off").strip().lower() or "off"
    excludes = _csv_list(os.getenv("SEMGREP_EXCLUDE"), DEFAULT_SECURITY_SCAN_EXCLUDES)
    if config == "auto" and metrics_mode == "off":
        # semgrep rejects auto config when metrics are forced off.
        metrics_mode = "auto"
    if metrics_mode not in {"on", "off", "auto"}:
        metrics_mode = "off"

    semgrep_args = [
        "scan",
        "--config",
        config,
        "--json",
        f"--metrics={metrics_mode}",
        "--error",
        ".",
    ]
    for excluded in excludes:
        semgrep_args.extend(["--exclude", excluded])
    if _command_exists("semgrep"):
        return _run_and_combine(["semgrep"] + semgrep_args, cwd=repo_path)

    if _docker_available():
        image = SEMGREP_DOCKER_IMAGE or "returntocorp/semgrep:latest"
        return _run_and_combine(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{repo_path}:/src",
                "-w",
                "/src",
                image,
                "semgrep",
            ]
            + semgrep_args
        )

    return False, "Semgrep not available (missing local semgrep binary and docker fallback)."


def strat_bandit_scan(repo_path: str, repo_analysis: dict):
    if not repo_analysis.get("has_python_files"):
        return False, "No Python files found for bandit strategy."

    level_map = {"low": "-l", "medium": "-ll", "high": "-lll"}
    confidence_map = {"low": "-i", "medium": "-ii", "high": "-iii"}
    min_level = os.getenv("BANDIT_MIN_SEVERITY", "medium").strip().lower()
    min_conf = os.getenv("BANDIT_MIN_CONFIDENCE", "medium").strip().lower()
    excludes = _csv_list(os.getenv("BANDIT_EXCLUDE"), BANDIT_DEFAULT_EXCLUDES)
    excludes_csv = ",".join(excludes)

    tracked_files = _git_tracked_python_files(repo_path)
    bandit_args = ["-f", "txt", "-q", level_map.get(min_level, "-ll"), confidence_map.get(min_conf, "-ii")]
    if tracked_files:
        bandit_args.extend(tracked_files)
    else:
        bandit_args.extend(["-r", "."])
        if excludes_csv:
            bandit_args.extend(["-x", excludes_csv])

    if _command_exists("bandit"):
        return _run_and_combine(["bandit"] + bandit_args, cwd=repo_path)

    if _docker_available():
        bandit_cmd = "bandit " + " ".join(bandit_args)
        return _run_and_combine(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{repo_path}:/src",
                "-w",
                "/src",
                "python:3.11-slim",
                "bash",
                "-lc",
                "pip install --disable-pip-version-check --no-cache-dir bandit >/tmp/bandit-install.log 2>&1 && "
                f"{bandit_cmd}",
            ]
        )

    return False, "Bandit not available (missing local bandit binary and docker fallback)."


def strat_trivy_scan(repo_path: str):
    severity = os.getenv("TRIVY_SEVERITY", "HIGH,CRITICAL").strip() or "HIGH,CRITICAL"
    scanners = os.getenv("TRIVY_SCANNERS", "vuln,misconfig,secret").strip() or "vuln,misconfig,secret"
    timeout = os.getenv("TRIVY_TIMEOUT", "5m").strip() or "5m"
    skip_dirs = _csv_list(os.getenv("TRIVY_SKIP_DIRS"), DEFAULT_SECURITY_SCAN_EXCLUDES)
    skip_files = _csv_list(os.getenv("TRIVY_SKIP_FILES"), ("ai-agent/github_app/github-app.pem",))

    trivy_args = [
        "fs",
        "--severity",
        severity,
        "--exit-code",
        "1",
        "--no-progress",
        "--timeout",
        timeout,
    ]
    if os.getenv("TRIVY_IGNORE_UNFIXED", "true").strip().lower() in _TRUTHY:
        trivy_args.append("--ignore-unfixed")
    for path in skip_dirs:
        trivy_args.extend(["--skip-dirs", path])
    for path in skip_files:
        trivy_args.extend(["--skip-files", path])

    scanners_legacy = ",".join(
        "config" if item.strip().lower() == "misconfig" else item.strip()
        for item in scanners.split(",")
        if item.strip()
    ) or "vuln,config,secret"

    def _run_trivy_local(extra_args: list[str]):
        return _run_and_combine(["trivy"] + trivy_args + extra_args + ["."], cwd=repo_path)

    def _run_trivy_docker(extra_args: list[str]):
        image = TRIVY_DOCKER_IMAGE or "aquasec/trivy:latest"
        return _run_and_combine(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{repo_path}:/src",
                "-w",
                "/src",
                image,
            ]
            + trivy_args
            + extra_args
            + ["/src"]
        )

    if _command_exists("trivy"):
        ok, out = _run_trivy_local(["--scanners", scanners])
        if "unknown flag: --scanners" in (out or "").lower():
            return _run_trivy_local(["--security-checks", scanners_legacy])
        return ok, out

    if _docker_available():
        ok, out = _run_trivy_docker(["--scanners", scanners])
        if "unknown flag: --scanners" in (out or "").lower():
            return _run_trivy_docker(["--security-checks", scanners_legacy])
        return ok, out

    return False, "Trivy not available (missing local trivy binary and docker fallback)."


def _normalize_sonar_project_key(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._:-" else "-" for ch in str(value or "").strip())
    safe = safe.strip("-._:")
    if not safe:
        safe = "codingai"
    return safe[:120]


def strat_sonarqube_scan(repo_path: str, repo_name: str):
    host = os.getenv("SONAR_HOST_URL", "").strip()
    token = os.getenv("SONAR_TOKEN", "").strip()
    project_key = _normalize_sonar_project_key(
        os.getenv("SONAR_PROJECT_KEY", "").strip() or repo_name or os.path.basename(repo_path)
    )
    quality_gate_wait = os.getenv("SONAR_QUALITY_GATE_WAIT", "false").strip().lower() in _TRUTHY

    if not host or not token:
        return False, "SonarQube strategy requires SONAR_HOST_URL and SONAR_TOKEN."

    scanner_args = [
        f"-Dsonar.host.url={host}",
        f"-Dsonar.token={token}",
        f"-Dsonar.projectKey={project_key}",
        "-Dsonar.projectBaseDir=.",
        "-Dsonar.sources=.",
    ]
    if quality_gate_wait:
        scanner_args.append("-Dsonar.qualitygate.wait=true")

    if _command_exists("sonar-scanner"):
        return _run_and_combine(["sonar-scanner"] + scanner_args, cwd=repo_path)

    if _docker_available():
        image = SONAR_SCANNER_DOCKER_IMAGE or "sonarsource/sonar-scanner-cli:latest"
        env = os.environ.copy()
        env["SONAR_HOST_URL"] = host
        env["SONAR_TOKEN"] = token
        return _run_and_combine(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{repo_path}:/usr/src",
                "-w",
                "/usr/src",
                "-e",
                f"SONAR_HOST_URL={host}",
                "-e",
                f"SONAR_TOKEN={token}",
                image,
                "sonar-scanner",
            ]
            + scanner_args,
            env=env,
        )

    return False, "SonarQube scanner not available (missing sonar-scanner binary and docker fallback)."


def _wait_for_http(url: str, timeout_seconds: int, *, proc: subprocess.Popen | None = None):
    checked_url = _validate_http_url(url)
    deadline = time.time() + max(1, int(timeout_seconds))
    last_error = ""

    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            return False, f"Server exited early with code {proc.returncode}."
        try:
            response = requests.get(checked_url, timeout=2, allow_redirects=True)
            status = int(response.status_code)
            if status < 500:
                return True, f"HTTP {status}"
            last_error = f"HTTP {status}"
        except requests.RequestException as e:
            last_error = str(e)
        except Exception as e:
            last_error = str(e)
        time.sleep(1)

    return False, f"Timed out waiting for {checked_url}. last_error={last_error}"


def _http_get_json(url: str, timeout: int = 15):
    checked_url = _validate_http_url(url)
    response = requests.get(checked_url, timeout=max(1, int(timeout)), allow_redirects=True)
    response.raise_for_status()
    return response.json()


def _dashboard_repo_value(repos, repo_name: str, field: str):
    for repo in repos:
        if isinstance(repo, dict) and repo.get("repo") == repo_name:
            return repo.get(field)
    return None


def _playwright_dashboard_checks(page, base_url: str):
    lines = ["--- codingai_dashboard_checks ---"]
    ok = True

    target_repo = ""
    setup_values_before = {}
    should_restore_github = False
    original_global_rules = ""
    original_project_rules = ""
    original_project_exists = False

    try:
        page.evaluate(
            """
            (() => {
              if (window.state && window.state.refreshTimer) {
                clearInterval(window.state.refreshTimer);
                window.state.refreshTimer = null;
              }
            })();
            """
        )

        repos_before = _http_get_json(f"{base_url.rstrip('/')}/repos")
        if not isinstance(repos_before, list) or not repos_before:
            return False, ["dashboard_check_error=no repos found from /repos"]
        preferred = next((repo for repo in repos_before if str(repo.get("repo") or "") == "testforai"), repos_before[0])
        target_repo = str(preferred.get("repo") or "")
        if not target_repo:
            return False, ["dashboard_check_error=empty target repo from /repos"]
        lines.append(f"target_repo={target_repo}")

        select_target = page.locator(f"#repoList button[data-repo='{target_repo}'][data-action='select']")
        if select_target.count() > 0:
            select_target.first.click(timeout=5000)
            page.wait_for_timeout(800)

        setup_values_before = _http_get_json(f"{base_url.rstrip('/')}/setup/values")
        owner_before = str(setup_values_before.get("owner") or "")
        app_id_before = str(setup_values_before.get("app_id") or "")
        install_id_before = str(setup_values_before.get("installation_id") or "")
        should_restore_github = bool(owner_before and app_id_before and install_id_before)

        repository_header_strong_exists = page.locator("strong#selectedRepoTitle").count() > 0
        lines.append(f"repository_header_strong_exists={repository_header_strong_exists}")
        if repository_header_strong_exists:
            ok = False

        queue_box = page.locator(".main-panel .content .box").first
        queue_actions_present = all(
            queue_box.get_by_role("button", name=label).count() > 0
            for label in ("Run Once", "Start Worker", "Stop Worker", "Refresh")
        )
        lines.append(f"queue_actions_present={queue_actions_present}")
        if not queue_actions_present:
            ok = False

<<<<<<< ui-notes-local-llm
        tracked_cards = page.locator("#trackedWrap .issue")
        tracked_cards_count = tracked_cards.count()
        lines.append(f"tracked_cards_count={tracked_cards_count}")
        if tracked_cards_count > 0:
            prompt_field_present = tracked_cards.first.locator("textarea[data-prompt]").count() > 0
            rerun_button_present = tracked_cards.first.get_by_role("button", name="Send + Rerun").count() > 0
            lines.append(f"tracked_prompt_field_present={prompt_field_present}")
            lines.append(f"tracked_rerun_button_present={rerun_button_present}")
            if not prompt_field_present or not rerun_button_present:
                ok = False

=======
>>>>>>> localstate
        notes_visible_initial = page.locator("#notesEditor").first.is_visible()
        lines.append(f"notes_visible_initial={notes_visible_initial}")
        if notes_visible_initial:
            ok = False

        page.locator("#btnOpenNotesEditor").first.click(timeout=5000)
        page.wait_for_timeout(350)
        notes_visible_after_open = page.locator("#notesEditor").first.is_visible()
        lines.append(f"notes_visible_after_open={notes_visible_after_open}")
        if not notes_visible_after_open:
            ok = False

        page.locator("#btnCloseNotesEditor").first.click(timeout=5000)
        page.wait_for_timeout(350)
        notes_visible_after_close = page.locator("#notesEditor").first.is_visible()
        lines.append(f"notes_visible_after_close={notes_visible_after_close}")
        if notes_visible_after_close:
            ok = False

        # Expand settings section if collapsed.
        settings_section = page.locator(".collapsible[data-section='settings']").first
        settings_collapsed = settings_section.evaluate("node => node.classList.contains('collapsed')")
        if settings_collapsed:
            settings_section.locator(".collapse-toggle").first.click(timeout=5000)
            page.wait_for_timeout(500)

        # Sidebar hide/reopen behavior.
        page.locator("#sidebarCollapseBtn").first.click(timeout=5000)
        page.wait_for_timeout(500)
        hidden_after_collapse = page.evaluate("document.body.classList.contains('sidebar-hidden')")
        lines.append(f"sidebar_hidden_after_toggle={hidden_after_collapse}")
        if not hidden_after_collapse:
            ok = False

        reopen_visible = page.locator("#sidebarReopenBtn").first.is_visible()
        lines.append(f"sidebar_reopen_visible={reopen_visible}")
        if not reopen_visible:
            ok = False

        page.locator("#sidebarReopenBtn").first.click(timeout=5000)
        page.wait_for_timeout(500)
        hidden_after_reopen = page.evaluate("document.body.classList.contains('sidebar-hidden')")
        lines.append(f"sidebar_hidden_after_reopen={hidden_after_reopen}")
        if hidden_after_reopen:
            ok = False

        # Drag-to-resize behavior.
        width_before = int(
            page.evaluate(
                "parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-width')) || 0"
            )
        )
        handle_box = page.locator("#sidebarHandle").first.bounding_box()
        width_after = width_before
        if handle_box:
            start_x = int(handle_box["x"] + (handle_box["width"] / 2.0))
            start_y = int(handle_box["y"] + 40)
            for delta in (140, -120, 200):
                page.mouse.move(start_x, start_y)
                page.mouse.down()
                page.mouse.move(int(start_x + delta), start_y, steps=12)
                page.mouse.up()
                page.wait_for_timeout(250)
                width_after = int(
                    page.evaluate(
                        "parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-width')) || 0"
                    )
                )
                if width_after != width_before:
                    break
        else:
            lines.append("sidebar_resize_handle_missing=true")
        lines.append(f"sidebar_width_before={width_before}")
        lines.append(f"sidebar_width_after={width_after}")
        if width_before == width_after:
            width_after = int(
                page.evaluate(
                    """
                    (() => {
                      const handle = document.getElementById('sidebarHandle');
                      const sidebar = document.getElementById('sidebar');
                      if (!handle || !sidebar) {
                        return parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-width')) || 0;
                      }
                      const rect = sidebar.getBoundingClientRect();
                      const startX = Math.round(rect.left + rect.width - 2);
                      const startY = Math.round(rect.top + 40);
                      handle.dispatchEvent(new MouseEvent('mousedown', { clientX: startX, clientY: startY, bubbles: true }));
                      document.dispatchEvent(new MouseEvent('mousemove', { clientX: startX + 160, clientY: startY, bubbles: true }));
                      document.dispatchEvent(new MouseEvent('mouseup', { clientX: startX + 160, clientY: startY, bubbles: true }));
                      return parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-width')) || 0;
                    })()
                    """
                )
            )
            lines.append("sidebar_resize_dispatch_fallback_used=true")
            lines.append(f"sidebar_width_after_dispatch={width_after}")
        if width_before == width_after:
            ok = False

        # Settings tab switch + workers pane check.
        workers_tab = page.locator(".settings-tab[data-settings-tab='workers']").first
        workers_tab.scroll_into_view_if_needed(timeout=5000)
        try:
            workers_tab.click(timeout=5000)
        except Exception:
            workers_tab.click(timeout=5000, force=True)
        page.wait_for_timeout(800)
        workers_meta = (page.locator("#workersSettingsMeta").first.inner_text() or "").strip()
        lines.append(f"workers_settings_meta={workers_meta}")
        if "workers:" not in workers_meta.lower():
            ok = False

        # GitHub IDs mask/show + button labels.
        page.locator(".settings-tab[data-settings-tab='github']").first.click(timeout=5000)
        page.wait_for_timeout(500)
        gh_app_type_before = page.locator("#ghAppId").first.evaluate("node => node.type")
        gh_install_type_before = page.locator("#ghInstallationId").first.evaluate("node => node.type")
        lines.append(f"github_app_input_type_before={gh_app_type_before}")
        lines.append(f"github_install_input_type_before={gh_install_type_before}")
        if gh_app_type_before != "password" or gh_install_type_before != "password":
            ok = False

        page.locator("#btnShowAppId").first.click(timeout=5000)
        page.wait_for_timeout(120)
        gh_app_type_shown = page.locator("#ghAppId").first.evaluate("node => node.type")
        lines.append(f"github_app_input_type_after_show={gh_app_type_shown}")
        if gh_app_type_shown != "text":
            ok = False
        page.locator("#btnShowAppId").first.click(timeout=5000)
        page.wait_for_timeout(120)

        update_btn_exists = page.get_by_role("button", name="Update GitHub IDs").count() > 0
        clear_btn_exists = page.get_by_role("button", name="Clear GitHub IDs").count() > 0
        lines.append(f"github_update_button_exists={update_btn_exists}")
        lines.append(f"github_clear_button_exists={clear_btn_exists}")
        if not update_btn_exists or not clear_btn_exists:
            ok = False

        # Clear GitHub IDs, then restore if previous values existed.
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator("#btnClearGithubIds").first.click(timeout=5000)
        page.wait_for_timeout(1400)
        setup_values_after_clear = _http_get_json(f"{base_url.rstrip('/')}/setup/values")
        cleared_owner = str(setup_values_after_clear.get("owner") or "")
        cleared_app = str(setup_values_after_clear.get("app_id") or "")
        cleared_install = str(setup_values_after_clear.get("installation_id") or "")
        lines.append(f"github_owner_after_clear={cleared_owner}")
        lines.append(f"github_app_after_clear={cleared_app}")
        lines.append(f"github_install_after_clear={cleared_install}")
        if cleared_owner or cleared_app or cleared_install:
            ok = False

        if should_restore_github:
            page.locator("#ghOwner").first.fill(owner_before, timeout=3000)
            page.locator("#ghAppId").first.fill(app_id_before, timeout=3000)
            page.locator("#ghInstallationId").first.fill(install_id_before, timeout=3000)
            page.get_by_role("button", name="Update GitHub IDs").first.click(timeout=5000)
            page.wait_for_timeout(1200)
            restored = _http_get_json(f"{base_url.rstrip('/')}/setup/values")
            owner_restored = str(restored.get("owner") or "")
            app_restored = str(restored.get("app_id") or "")
            install_restored = str(restored.get("installation_id") or "")
            lines.append(f"github_owner_restored={owner_restored}")
            lines.append(f"github_app_restored={app_restored}")
            lines.append(f"github_install_restored={install_restored}")
            if owner_restored != owner_before or app_restored != app_id_before or install_restored != install_id_before:
                ok = False

        # Re-select target repo after setup changes may have reset current selection.
        select_after_setup = page.locator(f"#repoList button[data-repo='{target_repo}'][data-action='select']")
        if select_after_setup.count() > 0:
            select_after_setup.first.click(timeout=5000)
            page.wait_for_timeout(900)

        # LLM settings save/read.
        page.locator(".settings-tab[data-settings-tab='llm']").first.click(timeout=5000)
        page.wait_for_timeout(500)
        llm_provider = str(setup_values_before.get("llm_provider") or "openai").strip().lower()
        order_raw = str(setup_values_before.get("llm_provider_order") or "local,openai").strip().lower()
        order_parts = [p.strip() for p in order_raw.split(",") if p.strip() in {"openai", "local"}]
        order_seen = set()
        order_norm = []
        for part in order_parts:
            if part in order_seen:
                continue
            order_seen.add(part)
            order_norm.append(part)
        llm_provider_order = ",".join(order_norm) or "local,openai"
        openai_base_url = str(setup_values_before.get("openai_base_url") or "https://api.openai.com").strip()
        local_llm_base_url = str(setup_values_before.get("local_llm_base_url") or "http://127.0.0.1:11434").strip()
        local_llm_api_mode = str(setup_values_before.get("local_llm_api_mode") or "chat").strip().lower()
        local_llm_profile = str(setup_values_before.get("local_llm_profile") or "auto").strip().lower()
        if local_llm_profile not in {"auto", "gpu16", "gpu24", "cpu"}:
            local_llm_profile = "auto"
        local_llm_model = str(setup_values_before.get("local_llm_model") or "").strip()

        llm_form = page.locator("#setupLlmForm")
        if llm_form.count() > 0:
            if llm_provider in {"openai", "local", "auto"}:
                page.locator("#llmProvider").first.select_option(llm_provider)
            page.locator("#llmProviderOrder").first.fill(llm_provider_order, timeout=3000)
            page.locator("#openaiBaseUrl").first.fill(openai_base_url, timeout=3000)
            page.locator("#localLlmBaseUrl").first.fill(local_llm_base_url, timeout=3000)
            if local_llm_api_mode in {"chat", "responses"}:
                page.locator("#localLlmApiMode").first.select_option(local_llm_api_mode)
            page.locator("#localLlmProfile").first.select_option(local_llm_profile)
            page.locator("#localLlmModel").first.fill(local_llm_model, timeout=3000)
            page.locator("#openaiApiKey").first.fill("", timeout=3000)
            page.locator("#localLlmApiKey").first.fill("", timeout=3000)

            openai_clear = page.locator("#openaiApiKeyClear").first
            if openai_clear.is_checked():
                openai_clear.uncheck(timeout=2000)
            local_clear = page.locator("#localLlmApiKeyClear").first
            if local_clear.is_checked():
                local_clear.uncheck(timeout=2000)

            page.get_by_role("button", name="Save LLM Settings").click(timeout=5000)
            page.wait_for_timeout(2000)

            setup_values_after = _http_get_json(f"{base_url.rstrip('/')}/setup/values")
            provider_after = str(setup_values_after.get("llm_provider") or "").strip().lower()
            provider_order_after = str(setup_values_after.get("llm_provider_order") or "").strip().lower()
            openai_base_after = str(setup_values_after.get("openai_base_url") or "").strip().rstrip("/")
            local_base_after = str(setup_values_after.get("local_llm_base_url") or "").strip().rstrip("/")
            local_mode_after = str(setup_values_after.get("local_llm_api_mode") or "").strip().lower()
            local_profile_after = str(setup_values_after.get("local_llm_profile") or "").strip().lower()
            local_model_after = str(setup_values_after.get("local_llm_model") or "").strip()

            lines.append(f"llm_provider_after={provider_after}")
            lines.append(f"llm_provider_order_after={provider_order_after}")
            lines.append(f"llm_openai_base_after={openai_base_after}")
            lines.append(f"llm_local_base_after={local_base_after}")
            lines.append(f"llm_local_mode_after={local_mode_after}")
            lines.append(f"llm_local_profile_after={local_profile_after}")

            if provider_after != llm_provider:
                ok = False
            if provider_order_after != llm_provider_order:
                ok = False
            if openai_base_after != openai_base_url.rstrip("/"):
                ok = False
            if local_base_after != local_llm_base_url.rstrip("/"):
                ok = False
            if local_mode_after != local_llm_api_mode:
                ok = False
            if local_profile_after != local_llm_profile:
                ok = False
            if local_model_after != local_llm_model:
                ok = False
        else:
            lines.append("llm_setup_check_skipped=form_not_found")

        # Run once
        page.get_by_role("button", name="Run Once").click(timeout=5000)
        page.wait_for_timeout(2200)
        lines.append("run_once_clicked=true")

        # Start worker
        page.get_by_role("button", name="Start Worker").click(timeout=5000)
        page.wait_for_timeout(2200)
        repos_after_start = _http_get_json(f"{base_url.rstrip('/')}/repos")
        running_after_start = _dashboard_repo_value(repos_after_start, target_repo, "running")
        lines.append(f"running_after_start={running_after_start}")
        if running_after_start is not True:
            ok = False

        # Stop worker
        page.get_by_role("button", name="Stop Worker").click(timeout=5000)
        page.wait_for_timeout(2200)
        repos_after_stop = _http_get_json(f"{base_url.rstrip('/')}/repos")
        running_after_stop = _dashboard_repo_value(repos_after_stop, target_repo, "running")
        lines.append(f"running_after_stop={running_after_stop}")
        if running_after_stop is not False:
            ok = False

        # Refresh
        page.get_by_role("button", name="Refresh").click(timeout=5000)
        page.wait_for_timeout(1200)
        lines.append("refresh_clicked=true")

        tracked_cards = page.locator("#trackedWrap .issue")
        tracked_count = tracked_cards.count()
        lines.append(f"tracked_cards_count={tracked_count}")
        if tracked_count > 0:
            prompt_box_count = tracked_cards.first.locator("textarea[data-prompt]").count()
            prompt_btn_count = tracked_cards.first.get_by_role("button", name="Send + Rerun").count()
            lines.append(f"tracked_prompt_box_present={prompt_box_count > 0}")
            lines.append(f"tracked_prompt_button_present={prompt_btn_count > 0}")
            if prompt_box_count == 0 or prompt_btn_count == 0:
                ok = False
        else:
            lines.append("tracked_prompt_check_skipped=no_cards")

        # Project toggle (enable/disable/enable)
        projects = _http_get_json(f"{base_url.rstrip('/')}/projects")
        proj_items = projects.get("projects", []) if isinstance(projects, dict) else []
        if proj_items:
            proj_name = str(proj_items[0].get("repo"))
            proj_enabled_initial = bool(proj_items[0].get("enabled"))
            lines.append(f"project_target={proj_name}")
            lines.append(f"project_initial_enabled={proj_enabled_initial}")

            # Ensure enabled before disable test.
            if not proj_enabled_initial:
                page.locator(f"#projectsList button[data-project='{proj_name}'][data-action='enable']").first.click(
                    timeout=5000
                )
                page.wait_for_timeout(2000)
            proj_after_enable = _http_get_json(f"{base_url.rstrip('/')}/projects")
            enabled_now = None
            for item in (proj_after_enable.get("projects", []) if isinstance(proj_after_enable, dict) else []):
                if str(item.get("repo")) == proj_name:
                    enabled_now = item.get("enabled")
                    break
            lines.append(f"project_after_enable={enabled_now}")
            if enabled_now is not True:
                ok = False

            page.locator(f"#projectsList button[data-project='{proj_name}'][data-action='disable']").first.click(
                timeout=5000
            )
            page.wait_for_timeout(2000)
            proj_after_disable = _http_get_json(f"{base_url.rstrip('/')}/projects")
            enabled_disabled = None
            for item in (proj_after_disable.get("projects", []) if isinstance(proj_after_disable, dict) else []):
                if str(item.get("repo")) == proj_name:
                    enabled_disabled = item.get("enabled")
                    break
            lines.append(f"project_after_disable={enabled_disabled}")
            if enabled_disabled is not False:
                ok = False

            page.locator(f"#projectsList button[data-project='{proj_name}'][data-action='enable']").first.click(
                timeout=5000
            )
            page.wait_for_timeout(2000)
            proj_after_reenable = _http_get_json(f"{base_url.rstrip('/')}/projects")
            enabled_reenabled = None
            for item in (proj_after_reenable.get("projects", []) if isinstance(proj_after_reenable, dict) else []):
                if str(item.get("repo")) == proj_name:
                    enabled_reenabled = item.get("enabled")
                    break
            lines.append(f"project_after_reenable={enabled_reenabled}")
            if enabled_reenabled is not True:
                ok = False
        else:
            lines.append("project_toggle_skipped=no projects")

        # Automation toggle
        auto_btn = page.locator(f"#repoList button[data-repo='{target_repo}'][data-action='auto-toggle']").first
        auto_text = (auto_btn.inner_text() or "").strip()
        if "Enable Auto" in auto_text:
            auto_btn.click(timeout=5000)
            page.wait_for_timeout(1800)
        repos_auto_on = _http_get_json(f"{base_url.rstrip('/')}/repos")
        automation_on = _dashboard_repo_value(repos_auto_on, target_repo, "automation")
        lines.append(f"automation_after_enable={automation_on}")
        if automation_on is not True:
            ok = False

        auto_btn2 = page.locator(f"#repoList button[data-repo='{target_repo}'][data-action='auto-toggle']").first
        auto_text2 = (auto_btn2.inner_text() or "").strip()
        if "Disable Auto" in auto_text2:
            auto_btn2.click(timeout=5000)
            page.wait_for_timeout(1800)
        repos_auto_off = _http_get_json(f"{base_url.rstrip('/')}/repos")
        automation_off = _dashboard_repo_value(repos_auto_off, target_repo, "automation")
        lines.append(f"automation_after_disable={automation_off}")
        if automation_off is not False:
            ok = False

        # Notes for LLM checks.
        select_for_rules = page.locator(f"#repoList button[data-repo='{target_repo}'][data-action='select']")
        if select_for_rules.count() > 0:
            select_for_rules.first.click(timeout=5000)
            page.wait_for_timeout(700)

        page.locator("#btnOpenNotesEditor").first.click(timeout=5000)
        page.wait_for_timeout(400)
        notes_visible_for_rules = page.locator("#notesEditor").first.is_visible()
        lines.append(f"notes_visible_for_rules={notes_visible_for_rules}")
        if not notes_visible_for_rules:
            ok = False

        global_before_resp = _http_get_json(f"{base_url.rstrip('/')}/rules/global")
        global_before = global_before_resp.get("rules", {}) if isinstance(global_before_resp, dict) else {}
        original_global_rules = str(global_before.get("rules_markdown") or "")

        project_before_resp = _http_get_json(f"{base_url.rstrip('/')}/rules/projects/{target_repo}")
        project_before = project_before_resp.get("rules", {}) if isinstance(project_before_resp, dict) else {}
        original_project_rules = str(project_before.get("rules_markdown") or "")
        original_project_exists = bool(project_before.get("exists"))

        global_probe = f"smoke-global-rule-{int(time.time())}"
        page.locator("#rulesGlobalInput").first.fill(global_probe, timeout=5000)
        page.locator("#btnSaveGlobalRules").first.click(timeout=5000)
        page.wait_for_timeout(1200)
        global_after_resp = _http_get_json(f"{base_url.rstrip('/')}/rules/global")
        global_after = global_after_resp.get("rules", {}) if isinstance(global_after_resp, dict) else {}
        global_saved = str(global_after.get("rules_markdown") or "")
        lines.append(f"rules_global_saved_match={global_saved == global_probe}")
        if global_saved != global_probe:
            ok = False

        project_probe = f"smoke-project-rule-{int(time.time())}"
        page.locator("#rulesProjectInput").first.fill(project_probe, timeout=5000)
        page.locator("#btnSaveProjectRules").first.click(timeout=5000)
        page.wait_for_timeout(1200)
        project_after_save_resp = _http_get_json(f"{base_url.rstrip('/')}/rules/projects/{target_repo}")
        project_after_save = project_after_save_resp.get("rules", {}) if isinstance(project_after_save_resp, dict) else {}
        project_saved = str(project_after_save.get("rules_markdown") or "")
        lines.append(f"rules_project_saved_match={project_saved == project_probe}")
        if project_saved != project_probe:
            ok = False

        page.locator("#btnResetProjectRules").first.click(timeout=5000)
        page.wait_for_timeout(1200)
        project_after_delete_resp = _http_get_json(f"{base_url.rstrip('/')}/rules/projects/{target_repo}")
        project_after_delete = project_after_delete_resp.get("rules", {}) if isinstance(project_after_delete_resp, dict) else {}
        project_deleted = not bool(project_after_delete.get("exists"))
        lines.append(f"rules_project_deleted={project_deleted}")
        if not project_deleted:
            ok = False

        effective_after_delete = _http_get_json(f"{base_url.rstrip('/')}/rules/effective?repo={target_repo}")
        effective_text_after_delete = str(effective_after_delete.get("text") or "")
        project_still_present = project_probe in effective_text_after_delete
        lines.append(f"rules_effective_project_probe_present_after_delete={project_still_present}")
        if project_still_present:
            ok = False

    except Exception as e:
        ok = False
        lines.append(f"dashboard_check_error={e}")
    finally:
        # Restore rule state after checks to avoid polluting runtime behavior.
        try:
            page.locator("#rulesGlobalInput").first.fill(original_global_rules, timeout=3000)
            page.locator("#btnSaveGlobalRules").first.click(timeout=5000)
            page.wait_for_timeout(600)
            lines.append("rules_restore_global=ok")
        except Exception as e:
            lines.append(f"rules_restore_global_error={e}")
            ok = False

        if target_repo:
            try:
                if original_project_exists:
                    page.locator("#rulesProjectInput").first.fill(original_project_rules, timeout=3000)
                    page.locator("#btnSaveProjectRules").first.click(timeout=5000)
                else:
                    page.locator("#btnResetProjectRules").first.click(timeout=5000)
                page.wait_for_timeout(600)
                lines.append("rules_restore_project=ok")
            except Exception as e:
                lines.append(f"rules_restore_project_error={e}")
                ok = False

    return ok, lines


def _playwright_smoke(base_url: str, project_dir: str, *, nav_timeout_ms: int, max_clicks: int):
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        return False, f"Playwright Python package unavailable: {e}"

    run_id = f"{_fingerprint(project_dir)}-{int(time.time())}"
    screenshot_path = os.path.join(tempfile.gettempdir(), f"codingai-web-smoke-{run_id}.png")
    browser_logs = []

    profile = os.getenv("WEB_SMOKE_PROFILE", "").strip().lower()

    def _one_run():
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()

            page.on("console", lambda msg: browser_logs.append(f"[console:{msg.type}] {msg.text}"))
            page.on("pageerror", lambda err: browser_logs.append(f"[pageerror] {err}"))

            resp = page.goto(base_url, wait_until="domcontentloaded", timeout=nav_timeout_ms)
            status = int(resp.status) if resp is not None and getattr(resp, "status", None) is not None else 0
            title = page.title() or ""
            dashboard_mode = profile in {"codingai", "codingai_dashboard"} or "codingai control plane" in title.lower()

            typed = False
            click_count = 0
            dashboard_ok = True
            dashboard_lines = []

            if dashboard_mode:
                d_ok, d_lines = _playwright_dashboard_checks(page, base_url)
                dashboard_ok = d_ok
                dashboard_lines = d_lines
                click_count = 5
            else:
                text_input = page.query_selector("input[type='text'],input:not([type]),textarea")
                if text_input is not None:
                    try:
                        text_input.fill("codingai smoke input", timeout=2500)
                        typed = True
                    except Exception as e:
                        browser_logs.append(f"[interaction:fill] {e}")

                clickable = page.query_selector_all("button,[role='button'],a[href],input[type='submit']")
                for idx, item in enumerate(clickable):
                    if idx >= max_clicks:
                        break
                    try:
                        if hasattr(item, "is_visible") and not item.is_visible():
                            continue
                        item.click(timeout=2500)
                        page.wait_for_timeout(350)
                        click_count += 1
                    except Exception as e:
                        browser_logs.append(f"[interaction:click:{idx}] {e}")

            page.screenshot(path=screenshot_path, full_page=True)
            url_after = page.url

            context.close()
            browser.close()

            return {
                "status": status,
                "title": title,
                "typed": typed,
                "click_count": click_count,
                "url_after": url_after,
                "screenshot": screenshot_path,
                "dashboard_mode": dashboard_mode,
                "dashboard_ok": dashboard_ok,
                "dashboard_lines": dashboard_lines,
            }

    try:
        result = _one_run()
    except Exception as e:
        err_text = str(e)
        if "playwright install" in err_text.lower() or "executable doesn't exist" in err_text.lower():
            install = _run([sys.executable, "-m", "playwright", "install", "chromium"])
            install_out = ((install.stdout or "") + "\n" + (install.stderr or "")).strip()
            if install.returncode != 0:
                return False, f"Playwright browser install failed:\n{install_out}"
            try:
                result = _one_run()
            except Exception as inner:
                return False, f"Playwright smoke run failed after install: {inner}"
        else:
            return False, f"Playwright smoke run failed: {e}"

    lines = [
        f"base_url={base_url}",
        f"http_status={result.get('status')}",
        f"title={result.get('title')}",
        f"dashboard_mode={result.get('dashboard_mode')}",
        f"dashboard_ok={result.get('dashboard_ok')}",
        f"typed_input={result.get('typed')}",
        f"clicked_elements={result.get('click_count')}",
        f"url_after={result.get('url_after')}",
        f"screenshot={result.get('screenshot')}",
    ]
    if result.get("dashboard_lines"):
        lines.extend(result.get("dashboard_lines"))
    if browser_logs:
        lines.append("--- browser logs ---")
        lines.extend(browser_logs[-80:])

    status = int(result.get("status", 0) or 0)
    ok = (status == 0 or (200 <= status < 400)) and bool(result.get("dashboard_ok", True))
    return ok, "\n".join(lines)


def strat_web_live_playwright(repo_path: str, repo_analysis: dict):
    base_url_override = os.getenv("WEB_SMOKE_BASE_URL", "").strip()
    nav_timeout_ms = max(5000, _safe_int(os.getenv("WEB_SMOKE_NAV_TIMEOUT_MS", "45000"), 45000))
    max_clicks = max(1, _safe_int(os.getenv("WEB_SMOKE_MAX_CLICKS", "6"), 6))

    if base_url_override:
        sections = [
            f"project_dir={repo_path}",
            "server_mode=external_base_url",
            f"base_url={base_url_override}",
        ]
        smoke_ok, smoke_out = _playwright_smoke(
            base_url=base_url_override,
            project_dir=repo_path,
            nav_timeout_ms=nav_timeout_ms,
            max_clicks=max_clicks,
        )
        sections.append("--- playwright smoke ---")
        sections.append(smoke_out)
        return smoke_ok, "\n".join([s for s in sections if s]).strip()

    pkg = find_first_web_package(repo_analysis)
    if not pkg:
        return False, (
            "No web node project detected for web_live_playwright strategy and "
            "WEB_SMOKE_BASE_URL is not set."
        )

    package_json = _load_package_json(pkg)
    if not package_json:
        return False, f"Could not read package.json: {pkg}"

    scripts = package_json.get("scripts", {})
    if not isinstance(scripts, dict):
        scripts = {}

    server_script = _pick_script(scripts, WEB_SERVER_SCRIPT_CANDIDATES)
    if not server_script:
        return False, (
            "No server script found in package.json. Expected one of: "
            + ", ".join(WEB_SERVER_SCRIPT_CANDIDATES)
        )

    e2e_script = _pick_script(scripts, WEB_TEST_SCRIPT_CANDIDATES)
    project_dir = os.path.dirname(pkg)
    host = os.getenv("WEB_SMOKE_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = _pick_free_local_port()
    base_url = f"http://{host}:{port}"
    startup_timeout = max(20, _safe_int(os.getenv("WEB_SMOKE_STARTUP_TIMEOUT_SECONDS", "90"), 90))

    sections = [
        f"project_dir={project_dir}",
        f"server_script={server_script}",
        f"e2e_script={e2e_script or ''}",
        f"base_url={base_url}",
    ]

    install = _run(["npm", "ci"], cwd=project_dir)
    install_out = ((install.stdout or "") + "\n" + (install.stderr or "")).strip()
    if install.returncode != 0:
        fallback = _run(["npm", "install"], cwd=project_dir)
        fallback_out = ((fallback.stdout or "") + "\n" + (fallback.stderr or "")).strip()
        sections.append("--- npm ci ---")
        sections.append(install_out)
        sections.append("--- npm install (fallback) ---")
        sections.append(fallback_out)
        if fallback.returncode != 0:
            return False, "\n".join([s for s in sections if s]).strip()

    if server_script == "start" and "build" in scripts:
        build = _run(["npm", "run", "build"], cwd=project_dir)
        build_out = ((build.stdout or "") + "\n" + (build.stderr or "")).strip()
        sections.append("--- npm run build ---")
        sections.append(build_out)
        if build.returncode != 0:
            return False, "\n".join([s for s in sections if s]).strip()

    server_env = os.environ.copy()
    server_env["HOST"] = host
    server_env["PORT"] = str(port)
    server_env["CI"] = "1"
    log_path = os.path.join(
        tempfile.gettempdir(),
        f"codingai-web-server-{_fingerprint(project_dir)}-{int(time.time())}.log",
    )

    server_variants = [
        ["npm", "run", server_script, "--", "--host", host, "--port", str(port)],
        ["npm", "run", server_script],
    ]
    server_proc = None
    started = False
    start_details = []

    try:
        with open(log_path, "a", encoding="utf-8") as log_f:
            for cmd in server_variants:
                log_f.write(f"\n=== server start command: {' '.join(cmd)} ===\n")
                log_f.flush()
                server_proc = subprocess.Popen(
                    cmd,
                    cwd=project_dir,
                    env=server_env,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                ready, detail = _wait_for_http(base_url, startup_timeout, proc=server_proc)
                start_details.append(f"cmd={' '.join(cmd)} -> {detail}")
                if ready:
                    started = True
                    break
                _terminate_process(server_proc)
                server_proc = None

        sections.append("--- server startup attempts ---")
        sections.extend(start_details)

        if not started:
            sections.append(f"server_log={log_path}")
            sections.append("--- server log tail ---")
            sections.append(_tail_text_file(log_path))
            return False, "\n".join([s for s in sections if s]).strip()

        smoke_ok, smoke_out = _playwright_smoke(
            base_url=base_url,
            project_dir=project_dir,
            nav_timeout_ms=nav_timeout_ms,
            max_clicks=max_clicks,
        )
        sections.append("--- playwright smoke ---")
        sections.append(smoke_out)

        if not smoke_ok:
            sections.append(f"server_log={log_path}")
            sections.append("--- server log tail ---")
            sections.append(_tail_text_file(log_path))
            return False, "\n".join([s for s in sections if s]).strip()

        if e2e_script:
            test_env = os.environ.copy()
            test_env["BASE_URL"] = base_url
            test_env["PLAYWRIGHT_BASE_URL"] = base_url
            test_env["HOST"] = host
            test_env["PORT"] = str(port)
            test_env["CI"] = "1"
            e2e = _run(["npm", "run", e2e_script], cwd=project_dir, env=test_env)
            e2e_out = ((e2e.stdout or "") + "\n" + (e2e.stderr or "")).strip()
            sections.append(f"--- npm run {e2e_script} ---")
            sections.append(e2e_out)
            if e2e.returncode != 0:
                sections.append(f"server_log={log_path}")
                sections.append("--- server log tail ---")
                sections.append(_tail_text_file(log_path))
                return False, "\n".join([s for s in sections if s]).strip()

        sections.append(f"server_log={log_path}")
        sections.append("--- server log tail ---")
        sections.append(_tail_text_file(log_path))
        return True, "\n".join([s for s in sections if s]).strip()
    finally:
        _terminate_process(server_proc)


def run_tests(
    repo_path: str,
    repo_name: str,
    max_attempts: int = 3,
    strategy_memory: dict | None = None,
    error_memory: dict | None = None,
    min_confidence_for_switch: float = 0.65,
    strategy_quarantine_threshold: int = 3,
    strategy_quarantine_seconds: int = 12 * 60 * 60,
    strategy_memory_half_life_seconds: int = 7 * 24 * 60 * 60,
):
    """
    LLM-guided adaptive runner:
    - create candidate strategies from repo analysis
    - run one
    - if fail, ask LLM which remaining strategy to try next based on error
    - hard cap attempts

    Returns:
      (success, output, report)
    """
    repo_analysis = analyze_repo(repo_path)
    memory = _normalize_strategy_memory(strategy_memory)
    error_memory = error_memory or {}
    now_ts = int(time.time())
    max_attempts = max(1, int(max_attempts))
    strategy_quarantine_threshold = max(1, _safe_int(strategy_quarantine_threshold, 3))
    strategy_quarantine_seconds = max(0, _safe_int(strategy_quarantine_seconds, 12 * 60 * 60))
    strategy_memory_half_life_seconds = max(0, _safe_int(strategy_memory_half_life_seconds, 7 * 24 * 60 * 60))

    report = {
        "result": "failed",
        "attempts": [],
        "llm_decisions": [],
        "max_attempts": max_attempts,
        "selected_strategy": None,
        "selected_strategy_confidence": 0.0,
        "selected_strategy_reason": "",
        "confidence_threshold_used": float(min_confidence_for_switch),
        "quarantined_strategies": [],
        "memory_policy": {
            "quarantine_threshold": strategy_quarantine_threshold,
            "quarantine_seconds": strategy_quarantine_seconds,
            "half_life_seconds": strategy_memory_half_life_seconds,
        },
    }

    # Build candidate strategy list based on what we actually found
    strategies = []

    if repo_analysis["has_docker_compose"]:
        strategies.append({"id": "docker_compose_build", "desc": "docker compose build", "kind": "native"})
        if DOCKER_COMPOSE_EPHEMERAL_UP_ENABLED:
            strategies.append(
                {
                    "id": "docker_compose_ephemeral_up",
                    "desc": "docker compose up in isolated ephemeral project",
                    "kind": "native",
                }
            )
    if repo_analysis["has_dockerfile_root"]:
        strategies.append({"id": "docker_build", "desc": "docker build (root Dockerfile)", "kind": "native"})
    if repo_analysis["dotnet_projects"]:
        strategies.append({"id": "dotnet_build_docker", "desc": "dotnet build in dotnet/sdk container", "kind": "container"})
        strategies.append({"id": "dotnet_build_docker_enable_windows_targeting", "desc": "dotnet build with EnableWindowsTargeting=true", "kind": "container"})
    if repo_analysis.get("web_node_projects") or os.getenv("WEB_SMOKE_BASE_URL", "").strip():
        strategies.append(
            {
                "id": "web_live_playwright",
                "desc": "launch web app and run Playwright live smoke interactions",
                "kind": "native",
            }
        )
    if repo_analysis["node_projects"]:
        strategies.append({"id": "node_build_docker", "desc": "node build in node:20 container", "kind": "container"})
    if (
        repo_analysis.get("dotnet_projects")
        or repo_analysis.get("node_projects")
        or repo_analysis.get("has_python_files")
        or repo_analysis.get("has_docker_compose")
        or repo_analysis.get("has_dockerfile_root")
    ):
        strategies.append({"id": "semgrep_scan", "desc": "semgrep static scan", "kind": "security"})
        strategies.append({"id": "trivy_scan", "desc": "trivy filesystem security scan", "kind": "security"})
        if repo_analysis.get("has_python_files"):
            strategies.append({"id": "bandit_scan", "desc": "bandit python security scan", "kind": "security"})
        if _sonarqube_configured():
            strategies.append({"id": "sonarqube_scan", "desc": "sonarqube scanner analysis", "kind": "security"})

    if not strategies:
        out = "No supported test strategy found (repo scan found no docker/dotnet/node/web markers)."
        report["final_error_fingerprint"] = _fingerprint(out)
        report["strategy_memory_update"] = memory
        return False, out, report

    attempted = []
    last_error = ""

    available = []
    quarantined = []
    for s in strategies:
        sid = s["id"]
        cooldown_until = int(memory.get(sid, {}).get("cooldown_until", 0))
        if cooldown_until > now_ts:
            quarantined.append(
                {
                    "strategy_id": sid,
                    "cooldown_until": cooldown_until,
                }
            )
            continue
        available.append(s)
    report["quarantined_strategies"] = quarantined

    # If all strategies are quarantined, we still proceed deterministically to avoid hard deadlocks.
    remaining = available[:] if available else strategies[:]
    if not remaining:
        out = "No remaining strategy candidates after quarantine filtering."
        report["final_error_fingerprint"] = _fingerprint(out)
        report["strategy_memory_update"] = memory
        return False, out, report

    # First pick: memory-biased if historical data exists; otherwise deterministic list order.
    memory_pick, memory_score = _pick_by_memory(
        remaining,
        memory,
        now_ts=now_ts,
        half_life_seconds=strategy_memory_half_life_seconds,
    )
    if memory_pick is not None:
        current = memory_pick
        last_selection_reason = f"Memory-biased selection (score={memory_score:.3f})."
        last_selection_confidence = min(1.0, max(0.0, memory_score))
    else:
        current = remaining.pop(0)
        last_selection_reason = "Deterministic first strategy selection."
        last_selection_confidence = 0.0

    for attempt in range(1, max_attempts + 1):
        attempted.append(current["id"])
        print(f"Test attempt {attempt}/{max_attempts}: {current['id']} ({current['desc']})")

        if current["id"] == "docker_compose_build":
            ok, out = strat_docker_compose_build(repo_path)
        elif current["id"] == "docker_compose_ephemeral_up":
            ok, out = strat_docker_compose_ephemeral_up(repo_path)
        elif current["id"] == "docker_build":
            ok, out = strat_docker_build(repo_path)
        elif current["id"] == "dotnet_build_docker":
            ok, out = strat_dotnet_build_docker(repo_path, repo_analysis)
        elif current["id"] == "dotnet_build_docker_enable_windows_targeting":
            ok, out = strat_dotnet_build_docker_enable_windows_targeting(repo_path, repo_analysis)
        elif current["id"] == "web_live_playwright":
            ok, out = strat_web_live_playwright(repo_path, repo_analysis)
        elif current["id"] == "node_build_docker":
            ok, out = strat_node_build_docker(repo_path, repo_analysis)
        elif current["id"] == "semgrep_scan":
            ok, out = strat_semgrep_scan(repo_path)
        elif current["id"] == "trivy_scan":
            ok, out = strat_trivy_scan(repo_path)
        elif current["id"] == "bandit_scan":
            ok, out = strat_bandit_scan(repo_path, repo_analysis)
        elif current["id"] == "sonarqube_scan":
            ok, out = strat_sonarqube_scan(repo_path, repo_name)
        else:
            ok, out = False, f"Unknown strategy id: {current['id']}"

        print(out)

        relevant_error = _extract_relevant_error_lines(out)
        _update_strategy_memory(
            memory,
            current["id"],
            ok,
            relevant_error,
            quarantine_threshold=strategy_quarantine_threshold,
            quarantine_seconds=strategy_quarantine_seconds,
        )

        attempt_entry = {
            "attempt": attempt,
            "strategy_id": current["id"],
            "strategy_desc": current["desc"],
            "result": "passed" if ok else "failed",
            "error_fingerprint": None if ok else _fingerprint(relevant_error or out),
        }
        report["attempts"].append(attempt_entry)

        if ok:
            report["result"] = "passed"
            report["selected_strategy"] = current["id"]
            report["selected_strategy_confidence"] = last_selection_confidence
            report["selected_strategy_reason"] = last_selection_reason
            report["strategy_memory_update"] = memory
            if report["attempts"]:
                updates = {}
                for a in report["attempts"]:
                    if a.get("result") == "failed" and a.get("error_fingerprint"):
                        updates[a["error_fingerprint"]] = {
                            "preferred_strategy": current["id"],
                            "updated_at": int(time.time()),
                        }
                if updates:
                    report["error_memory_update"] = updates
            return True, out, report

        last_error = relevant_error or out or "Unknown failure."

        if not remaining:
            break

        # Ask LLM what to try next
        decision = pick_next_strategy(
            repo_name=repo_name,
            repo_analysis=repo_analysis,
            last_error=last_error,
            attempted=attempted,
            remaining=remaining,
            strategy_memory=memory,
        )

        chosen_id = decision.get("next_strategy_id")
        llm_confidence = _safe_float(decision.get("confidence", 0.0), default=0.0)
        llm_reason = str(decision.get("reason", ""))[:500]
        report["llm_decisions"].append(
            {
                "attempt": attempt,
                "chosen_id": chosen_id,
                "confidence": llm_confidence,
                "reason": llm_reason,
                "retry_count": _safe_int(decision.get("retry_count", 0), 0),
            }
        )
        print(
            f"LLM decision: next={chosen_id} conf={llm_confidence} "
            f"retries={decision.get('retry_count', 0)} reason={llm_reason}"
        )

        idx = next((i for i, s in enumerate(remaining) if s["id"] == chosen_id), None)

        # Confidence threshold gate for switching to LLM-recommended strategy.
        if idx is not None and llm_confidence >= min_confidence_for_switch:
            current = remaining.pop(idx)
            last_selection_reason = f"LLM-selected (conf={llm_confidence:.2f}). {llm_reason}"
            last_selection_confidence = llm_confidence
            continue

        # History bias: if error fingerprint seen before with known winner strategy
        if attempt_entry["error_fingerprint"]:
            hist = error_memory.get(attempt_entry["error_fingerprint"])
            if hist:
                preferred = hist.get("preferred_strategy")
                idx_hist = next((i for i, s in enumerate(remaining) if s["id"] == preferred), None)
                if idx_hist is not None:
                    current = remaining.pop(idx_hist)
                    last_selection_reason = (
                        f"History-selected for fingerprint {attempt_entry['error_fingerprint']} -> {preferred}."
                    )
                    last_selection_confidence = 0.9
                    continue

        memory_pick, memory_score = _pick_by_memory(
            remaining,
            memory,
            now_ts=int(time.time()),
            half_life_seconds=strategy_memory_half_life_seconds,
        )
        if memory_pick is not None:
            current = memory_pick
            last_selection_reason = (
                f"LLM confidence {llm_confidence:.2f} below threshold {min_confidence_for_switch:.2f}; "
                f"memory-selected (score={memory_score:.3f})."
            )
            last_selection_confidence = min(1.0, max(0.0, memory_score))
            continue

        current = remaining.pop(0)
        last_selection_reason = (
            f"LLM confidence {llm_confidence:.2f} below threshold {min_confidence_for_switch:.2f}; "
            "fallback to first remaining strategy."
        )
        last_selection_confidence = 0.0

    # Return failure with fingerprint for state tracking
    trimmed_error = _extract_relevant_error_lines(last_error) or last_error
    failure = f"[fail:{_fingerprint(trimmed_error)}]\n{trimmed_error}"
    report["result"] = "failed"
    report["selected_strategy"] = report["attempts"][-1]["strategy_id"] if report["attempts"] else None
    report["selected_strategy_confidence"] = last_selection_confidence
    report["selected_strategy_reason"] = last_selection_reason
    report["final_error_fingerprint"] = _fingerprint(trimmed_error)
    report["strategy_memory_update"] = memory
    return False, failure, report
