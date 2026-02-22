import os
import json
import subprocess
import hashlib
from llm.strategy_llm import pick_next_strategy


def _run(cmd, *, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def _fingerprint(s: str) -> str:
    return hashlib.sha1((s or "").encode("utf-8", errors="ignore")).hexdigest()[:12]


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
                summary["node_projects"].append(os.path.join(root, f))

    # cap sizes
    summary["dotnet_projects"] = summary["dotnet_projects"][:20]
    summary["node_projects"] = summary["node_projects"][:20]
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


# ----------------------------
# Strategies
# ----------------------------

def strat_docker_compose_build(repo_path: str):
    res = _run(["docker", "compose", "build"], cwd=repo_path)
    out = (res.stdout or "") + "\n" + (res.stderr or "")
    return res.returncode == 0, out.strip()


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
            "dotnet", "build"
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
            "dotnet", "build", "-p:EnableWindowsTargeting=true"
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
            "npm ci || npm install; npm run build"
        ]
    )
    out = (res.stdout or "") + "\n" + (res.stderr or "")
    return res.returncode == 0, out.strip()


def run_tests(repo_path: str, repo_name: str, max_attempts: int = 3):
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
    report = {
        "result": "failed",
        "attempts": [],
        "selected_strategy": None,
        "selected_strategy_confidence": 0.0,
        "selected_strategy_reason": "",
    }

    # Build candidate strategy list based on what we actually found
    strategies = []

    if repo_analysis["has_docker_compose"]:
        strategies.append({"id": "docker_compose_build", "desc": "docker compose build", "kind": "native"})
    if repo_analysis["has_dockerfile_root"]:
        strategies.append({"id": "docker_build", "desc": "docker build (root Dockerfile)", "kind": "native"})
    if repo_analysis["dotnet_projects"]:
        strategies.append({"id": "dotnet_build_docker", "desc": "dotnet build in dotnet/sdk container", "kind": "container"})
        strategies.append({"id": "dotnet_build_docker_enable_windows_targeting", "desc": "dotnet build with EnableWindowsTargeting=true", "kind": "container"})
    if repo_analysis["node_projects"]:
        strategies.append({"id": "node_build_docker", "desc": "node build in node:20 container", "kind": "container"})

    if not strategies:
        out = "No supported test strategy found (repo scan found no docker/dotnet/node markers)."
        report["final_error_fingerprint"] = _fingerprint(out)
        return False, out, report

    attempted = []
    last_error = ""

    # Deterministic first pick: docker compose > dockerfile > dotnet > node (based on list order above)
    remaining = strategies[:]
    current = remaining.pop(0)
    last_selection_reason = "Deterministic first strategy selection."
    last_selection_confidence = 0.0

    for attempt in range(1, max_attempts + 1):
        attempted.append(current["id"])
        print(f"Test attempt {attempt}/{max_attempts}: {current['id']} ({current['desc']})")

        if current["id"] == "docker_compose_build":
            ok, out = strat_docker_compose_build(repo_path)
        elif current["id"] == "docker_build":
            ok, out = strat_docker_build(repo_path)
        elif current["id"] == "dotnet_build_docker":
            ok, out = strat_dotnet_build_docker(repo_path, repo_analysis)
        elif current["id"] == "dotnet_build_docker_enable_windows_targeting":
            ok, out = strat_dotnet_build_docker_enable_windows_targeting(repo_path, repo_analysis)
        elif current["id"] == "node_build_docker":
            ok, out = strat_node_build_docker(repo_path, repo_analysis)
        else:
            ok, out = False, f"Unknown strategy id: {current['id']}"

        print(out)

        attempt_entry = {
            "attempt": attempt,
            "strategy_id": current["id"],
            "strategy_desc": current["desc"],
            "result": "passed" if ok else "failed",
            "error_fingerprint": None if ok else _fingerprint(out),
        }
        report["attempts"].append(attempt_entry)

        if ok:
            report["result"] = "passed"
            report["selected_strategy"] = current["id"]
            report["selected_strategy_confidence"] = last_selection_confidence
            report["selected_strategy_reason"] = last_selection_reason
            return True, out, report

        last_error = out or "Unknown failure."

        if not remaining:
            break

        # Ask LLM what to try next
        decision = pick_next_strategy(
            repo_name=repo_name,
            repo_analysis=repo_analysis,
            last_error=last_error,
            attempted=attempted,
            remaining=remaining,
        )

        chosen_id = decision.get("next_strategy_id")
        print(f"LLM decision: next={chosen_id} conf={decision.get('confidence')} reason={decision.get('reason')}")

        # Select chosen or fallback
        idx = next((i for i, s in enumerate(remaining) if s["id"] == chosen_id), None)
        if idx is None:
            current = remaining.pop(0)
            last_selection_reason = "Fallback to first remaining strategy."
            last_selection_confidence = 0.0
        else:
            current = remaining.pop(idx)
            last_selection_reason = str(decision.get("reason", ""))[:500]
            try:
                last_selection_confidence = float(decision.get("confidence", 0.0))
            except Exception:
                last_selection_confidence = 0.0

    # Return failure with fingerprint for state tracking
    failure = f"[fail:{_fingerprint(last_error)}]\n{last_error}"
    report["result"] = "failed"
    report["selected_strategy"] = report["attempts"][-1]["strategy_id"] if report["attempts"] else None
    report["selected_strategy_confidence"] = last_selection_confidence
    report["selected_strategy_reason"] = last_selection_reason
    report["final_error_fingerprint"] = _fingerprint(last_error)
    return False, failure, report
