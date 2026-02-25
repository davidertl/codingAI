from orchestrator.contracts import TaskClassification

_TASK_TYPE_KEYWORDS = {
    "debug": {"bug", "fix", "error", "crash", "exception", "broken", "traceback", "failing", "regression"},
    "security": {"security", "vulnerability", "cve", "xss", "injection", "auth", "csrf", "exploit"},
    "refactor": {"refactor", "cleanup", "reorganize", "restructure", "simplify", "technical debt", "tech debt"},
    "test_writing": {"test", "coverage", "spec", "unit test", "integration test", "e2e"},
    "analyze": {"analyze", "investigate", "review", "audit", "report", "benchmark", "performance"},
}

_COMPLEXITY_HIGH_SIGNALS = {
    "architecture", "migration", "breaking change", "redesign", "multiple services",
    "database schema", "api versioning", "cross-cutting",
}
_COMPLEXITY_LOW_SIGNALS = {
    "typo", "rename", "comment", "readme", "docs", "label", "badge", "formatting",
}

_RISK_HIGH_SIGNALS = {"production", "auth", "payment", "security", "database", "migration", "critical"}


def classify_task(
    *,
    issue_title: str,
    issue_body: str,
    labels: list[str],
    repo_metadata: dict | None = None,
    file_tree_summary: str = "",
) -> TaskClassification:
    combined = f"{issue_title}\n{issue_body}\n{' '.join(labels)}".lower()

    task_type = "feature"
    best_score = 0
    for candidate, keywords in _TASK_TYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in combined)
        if score > best_score:
            best_score = score
            task_type = candidate

    complexity = "medium"
    if any(s in combined for s in _COMPLEXITY_HIGH_SIGNALS):
        complexity = "high"
    elif any(s in combined for s in _COMPLEXITY_LOW_SIGNALS):
        complexity = "low"

    risk_level = "medium"
    if any(s in combined for s in _RISK_HIGH_SIGNALS):
        risk_level = "high"
    if complexity == "low" and risk_level != "high":
        risk_level = "low"

    required_skills = []
    meta = repo_metadata or {}
    if meta.get("has_python_files"):
        required_skills.append("python")
    if meta.get("node_projects"):
        required_skills.append("nodejs")
    if meta.get("dotnet_projects"):
        required_skills.append("dotnet")
    if meta.get("has_docker_compose") or meta.get("has_dockerfile_root"):
        required_skills.append("docker")

    suggested_tools = []
    if task_type == "debug":
        suggested_tools.append("test_runner")
    if task_type == "security":
        suggested_tools.extend(["semgrep", "trivy"])
    if "research" in combined or "investigate" in combined:
        suggested_tools.append("searxng")

    summary_prompt = (
        f"[{task_type.upper()}] (complexity={complexity}, risk={risk_level}) "
        f"{issue_title.strip()}"
    )

    return TaskClassification(
        task_type=task_type,
        complexity=complexity,
        required_skills=required_skills,
        suggested_tools=suggested_tools,
        risk_level=risk_level,
        summary_prompt=summary_prompt,
    )
