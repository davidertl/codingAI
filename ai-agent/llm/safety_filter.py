import re
import logging

from core.observability import record_event

log = logging.getLogger(__name__)

_SECRET_PATTERNS = [
    re.compile(r"(?:api[_-]?key|apikey|secret|token|password|passwd|auth)\s*[:=]\s*['\"][A-Za-z0-9/+=_-]{16,}['\"]", re.I),
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
    re.compile(r"ghp_[A-Za-z0-9]{36}"),
    re.compile(r"gho_[A-Za-z0-9]{36}"),
    re.compile(r"sk-[A-Za-z0-9]{32,}"),
    re.compile(r"AIza[A-Za-z0-9_-]{35}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"xox[bpas]-[A-Za-z0-9-]+"),
]

_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|above|prior)\s+instructions", re.I),
    re.compile(r"disregard\s+(?:all\s+)?(?:previous|above|prior)", re.I),
    re.compile(r"you\s+are\s+now\s+(?:a|an|in)", re.I),
    re.compile(r"system\s*:\s*you\s+(?:are|must)", re.I),
    re.compile(r"<\|?(?:system|im_start|im_end)\|?>", re.I),
    re.compile(r"```\s*system\b", re.I),
    re.compile(r"(?:ADMIN|ROOT|SUDO)\s+override", re.I),
]


class SafetyFilterError(Exception):
    pass


class SafetyViolation:
    def __init__(self, category: str, description: str, severity: str = "warning"):
        self.category = category
        self.description = description
        self.severity = severity

    def __repr__(self):
        return f"SafetyViolation({self.category}: {self.description})"


def check_prompt_injection(text: str) -> list[SafetyViolation]:
    violations = []
    for pattern in _INJECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            violations.append(SafetyViolation(
                category="prompt_injection",
                description=f"Potential prompt injection detected: '{match.group()[:80]}'",
                severity="error",
            ))
    return violations


def check_secret_leak(text: str) -> list[SafetyViolation]:
    violations = []
    for pattern in _SECRET_PATTERNS:
        match = pattern.search(text)
        if match:
            snippet = match.group()[:20] + "..."
            violations.append(SafetyViolation(
                category="secret_leak",
                description=f"Potential secret detected: '{snippet}'",
                severity="error",
            ))
    return violations


def check_output_safety(text: str) -> list[SafetyViolation]:
    violations = check_secret_leak(text)
    return violations


def filter_input(text: str, *, repo: str = "", raise_on_violation: bool = False) -> tuple[str, list[SafetyViolation]]:
    violations = check_prompt_injection(text) + check_secret_leak(text)

    if violations:
        record_event(
            "safety_filter_violation",
            repo=repo,
            status="blocked" if raise_on_violation else "warning",
            data={
                "violations": [
                    {"category": v.category, "severity": v.severity, "description": v.description}
                    for v in violations
                ],
            },
        )
        if raise_on_violation:
            raise SafetyFilterError(
                f"Safety filter blocked input: {violations[0].description}"
            )

    return text, violations


def filter_output(text: str, *, repo: str = "") -> tuple[str, list[SafetyViolation]]:
    violations = check_output_safety(text)

    if violations:
        record_event(
            "safety_filter_output_violation",
            repo=repo,
            status="warning",
            data={
                "violations": [
                    {"category": v.category, "severity": v.severity}
                    for v in violations
                ],
            },
        )

    return text, violations
