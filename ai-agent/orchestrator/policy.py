import os
import re
from pathlib import Path

from orchestrator.contracts import CodeBundle
from orchestrator.errors import OrchestratorValidationError, SecurityPolicyViolationError

_DIFF_MARKERS = (
    "\n@@",
    "\ndiff --git",
    "\n--- ",
    "\n+++ ",
)
_PLACEHOLDER_PATTERNS = (
    r"\bTODO\b",
    r"\bFIXME\b",
    r"\bplaceholder\b",
    r"\bunchanged\b",
    r"\bexisting code\b",
    r"\bsnippet\b",
)
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}", re.IGNORECASE),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
]
_DEPENDENCY_FILES = {
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "requirements.txt",
    "pyproject.toml",
    "poetry.lock",
    "Pipfile",
    "Pipfile.lock",
    "go.mod",
    "go.sum",
    "Cargo.toml",
    "Cargo.lock",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
}


def contains_diff_like_markers(text: str) -> bool:
    value = str(text or "")
    if value.startswith("@@ ") or value.startswith("--- ") or value.startswith("+++ "):
        return True
    return any(marker in value for marker in _DIFF_MARKERS)


def contains_placeholder_content(text: str) -> bool:
    value = str(text or "")
    for pattern in _PLACEHOLDER_PATTERNS:
        if re.search(pattern, value, flags=re.IGNORECASE):
            return True
    return False


def find_secret_matches(text: str) -> list[str]:
    value = str(text or "")
    hits = []
    for pattern in _SECRET_PATTERNS:
        match = pattern.search(value)
        if match:
            hits.append(pattern.pattern)
    return hits


def ensure_complete_file_only_bundle(bundle: CodeBundle):
    if not isinstance(bundle, CodeBundle):
        raise OrchestratorValidationError("bundle must be a CodeBundle")
    if not bundle.files:
        raise OrchestratorValidationError("bundle must include at least one file")
    for artifact in bundle.files:
        if contains_diff_like_markers(artifact.content):
            raise OrchestratorValidationError(f"{artifact.path}: diff markers are not allowed")
        if contains_placeholder_content(artifact.content):
            raise OrchestratorValidationError(f"{artifact.path}: placeholder style content detected")


def ensure_no_secrets(bundle: CodeBundle):
    for artifact in bundle.files:
        matches = find_secret_matches(artifact.content)
        if matches:
            raise SecurityPolicyViolationError(
                f"{artifact.path}: potential secret material detected ({', '.join(matches[:3])})"
            )


def ensure_dependency_policy(bundle: CodeBundle, *, dependency_change_approved: bool):
    touched = [f.path for f in bundle.files if os.path.basename(f.path) in _DEPENDENCY_FILES]
    touched.extend([p for p in bundle.delete_paths if os.path.basename(p) in _DEPENDENCY_FILES])
    if touched and not dependency_change_approved:
        raise SecurityPolicyViolationError(
            "dependency file changes are blocked unless explicitly approved: " + ", ".join(sorted(touched))
        )


def ensure_paths_within_repo(bundle: CodeBundle, repo_root: str):
    root = Path(repo_root).resolve()
    for artifact in bundle.files:
        target = (root / artifact.path).resolve()
        if target == root:
            raise OrchestratorValidationError(f"{artifact.path}: invalid repository path target")
        if not str(target).startswith(str(root) + os.sep):
            raise OrchestratorValidationError(f"{artifact.path}: path escapes repository root")
    for path in bundle.delete_paths:
        target = (root / path).resolve()
        if target == root:
            raise OrchestratorValidationError(f"{path}: invalid repository path target")
        if not str(target).startswith(str(root) + os.sep):
            raise OrchestratorValidationError(f"{path}: path escapes repository root")


def validate_code_bundle(
    bundle: CodeBundle,
    *,
    repo_root: str,
    dependency_change_approved: bool = False,
):
    ensure_complete_file_only_bundle(bundle)
    ensure_paths_within_repo(bundle, repo_root)
    ensure_no_secrets(bundle)
    ensure_dependency_policy(bundle, dependency_change_approved=dependency_change_approved)
    return {
        "complete_file_only": True,
        "paths_safe": True,
        "no_secrets": True,
        "dependency_policy": True,
    }
