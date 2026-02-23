from orchestrator.contracts import TestFailureAnalysis
from orchestrator.utils import fingerprint_text


def interpret_test_failure(*, exit_code: int, logs: str) -> TestFailureAnalysis:
    text = str(logs or "").strip()
    text_lower = text.lower()
    evidence = []
    root_type = "unknown"
    root_cause = "Unable to determine root cause from truncated logs."

    if "permission denied" in text_lower or "network is unreachable" in text_lower:
        root_type = "environment"
        root_cause = "Execution environment permission/network constraints caused the failure."
    elif "out of memory" in text_lower or "killed" in text_lower:
        root_type = "environment"
        root_cause = "Resource pressure (likely OOM) interrupted execution."
    elif any(token in text_lower for token in ("traceback", "assertionerror", "typeerror", "syntaxerror", "exception")):
        root_type = "code"
        root_cause = "Application/test code raised an exception."
    elif "error" in text_lower and "failed" in text_lower:
        root_type = "code"
        root_cause = "Build/test command failed with deterministic error output."

    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            continue
        if any(k in raw.lower() for k in ("error", "failed", "exception", "traceback", "fatal")):
            evidence.append(raw[:400])
        if len(evidence) >= 20:
            break

    if not evidence and text:
        evidence = [text[:400]]

    return TestFailureAnalysis(
        symptom=f"Test execution failed with exit_code={int(exit_code)} and fingerprint={fingerprint_text(text)}",
        root_cause=root_cause,
        root_cause_type=root_type,  # type: ignore[arg-type]
        evidence=evidence,
        recommended_actions=[
            "Use root-cause evidence to regenerate minimal patch",
            "Rerun sandbox profile after patch regeneration",
        ],
    )
