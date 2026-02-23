from orchestrator.contracts import CodeBundle, FileArtifact, TaskSpecification
from llm.patch_llm import propose_patch_ops


def generate_code_bundle(
    *,
    repo_path: str,
    repo_name: str,
    task_spec: TaskSpecification,
    issue: dict,
    repo_analysis: dict,
    max_ops: int = 20,
) -> tuple[CodeBundle, list[dict], dict]:
    patch_result = propose_patch_ops(
        repo_path=repo_path,
        repo_name=repo_name,
        issue=issue,
        repo_analysis=repo_analysis,
        max_ops=max_ops,
    )
    patch_ops = patch_result.get("patch_ops", []) if isinstance(patch_result, dict) else []
    files = []
    delete_paths = []
    for op in patch_ops:
        if not isinstance(op, dict):
            continue
        action = str(op.get("action") or "").strip().lower()
        path = str(op.get("path") or "").strip()
        if action in {"create", "update"}:
            files.append(FileArtifact(path=path, content=str(op.get("content") or "")))
        elif action == "delete":
            delete_paths.append(path)

    bundle = CodeBundle(
        files=files,
        delete_paths=delete_paths,
        rationale=str(patch_result.get("reason") or ""),
        assumptions=list(task_spec.ambiguity_notes or []),
        testing_notes=[
            "Run sandbox test profile",
            "Re-run after repair loop if tests fail",
        ],
    )
    meta = {
        "confidence": float(patch_result.get("confidence", 0.0) or 0.0),
        "reason": str(patch_result.get("reason") or ""),
        "patch_ops_count": len(patch_ops),
    }
    return bundle, patch_ops, meta
