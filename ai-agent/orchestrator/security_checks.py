import os

from orchestrator.contracts import CodeBundle, ReviewReport, TaskSpecification


def detect_api_surface_changes(bundle: CodeBundle) -> dict:
    touched = [artifact.path for artifact in bundle.files] + list(bundle.delete_paths)
    api_files = [path for path in touched if path == "ai-agent/service/api.py" or path.endswith("/service/api.py")]
    return {
        "touched": bool(api_files),
        "files": sorted(api_files),
    }


def detect_dependency_changes(bundle: CodeBundle) -> dict:
    dependency_names = {
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
    touched = [artifact.path for artifact in bundle.files]
    files = [path for path in touched if os.path.basename(path) in dependency_names]
    return {"touched": bool(files), "files": sorted(files)}


def validate_success_criteria(
    *,
    task_spec: TaskSpecification,
    bundle: CodeBundle,
    review: ReviewReport,
    tests_passed: bool,
    build_passed: bool,
    dependency_change_approved: bool = False,
    api_breaking_change_approved: bool = False,
) -> dict[str, bool]:
    dep = detect_dependency_changes(bundle)
    api = detect_api_surface_changes(bundle)
    review_ok = not review.has_errors

    return {
        "build_succeeds": bool(build_passed),
        "tests_pass": bool(tests_passed),
        "no_new_dependencies": (not dep["touched"]) or bool(dependency_change_approved),
        "no_secrets_detected": bool(review.assertions.get("no_secret_candidates", True)),
        "no_api_breaking_changes": (not api["touched"]) or bool(api_breaking_change_approved),
        "acceptance_criteria_fulfilled": bool(task_spec.acceptance_criteria) and review_ok and bool(tests_passed),
    }
