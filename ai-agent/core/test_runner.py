import hashlib
import os
import subprocess
import time

from llm.strategy_llm import pick_next_strategy


def _run(cmd, *, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def _fingerprint(s: str) -> str:
    return hashlib.sha1((s or "").encode("utf-8", errors="ignore")).hexdigest()[:12]


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
        }
    return normalized


def _strategy_memory_score(strategy_id: str, memory: dict) -> float:
    stats = memory.get(strategy_id, {})
    runs = int(stats.get("runs", 0))
    successes = int(stats.get("successes", 0))

    # Laplace-smoothed success rate, slightly favor proven history.
    success_rate = (successes + 1.0) / (runs + 2.0)
    experience_bonus = min(runs, 20) * 0.01
    return success_rate + experience_bonus


def _pick_by_memory(remaining: list, memory: dict):
    with_history = []
    for i, s in enumerate(remaining):
        sid = s["id"]
        runs = int(memory.get(sid, {}).get("runs", 0))
        if runs <= 0:
            continue
        score = _strategy_memory_score(sid, memory)
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


def _update_strategy_memory(memory: dict, strategy_id: str, ok: bool, error_text: str):
    stats = memory.setdefault(
        strategy_id,
        {
            "runs": 0,
            "successes": 0,
            "failures": 0,
            "last_result": "",
            "last_error_fingerprint": "",
            "updated_at": 0,
        },
    )

    stats["runs"] += 1
    if ok:
        stats["successes"] += 1
        stats["last_result"] = "passed"
        stats["last_error_fingerprint"] = ""
    else:
        stats["failures"] += 1
        stats["last_result"] = "failed"
        stats["last_error_fingerprint"] = _fingerprint(error_text)
    stats["updated_at"] = int(time.time())


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


def run_tests(
    repo_path: str,
    repo_name: str,
    max_attempts: int = 3,
    strategy_memory: dict | None = None,
    min_confidence_for_switch: float = 0.65,
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

    report = {
        "result": "failed",
        "attempts": [],
        "selected_strategy": None,
        "selected_strategy_confidence": 0.0,
        "selected_strategy_reason": "",
        "confidence_threshold_used": float(min_confidence_for_switch),
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
        report["strategy_memory_update"] = memory
        return False, out, report

    attempted = []
    last_error = ""

    # First pick: memory-biased if historical data exists; otherwise deterministic list order.
    remaining = strategies[:]
    memory_pick, memory_score = _pick_by_memory(remaining, memory)
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

        relevant_error = _extract_relevant_error_lines(out)
        _update_strategy_memory(memory, current["id"], ok, relevant_error)

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

        memory_pick, memory_score = _pick_by_memory(remaining, memory)
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
