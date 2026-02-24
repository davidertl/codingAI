"""
Deterministic prompt-safety filter for LLM inputs.

Blocks known exfiltration, jailbreak, and secret-retrieval patterns before
any text is sent to an LLM provider.  Designed as a lightweight first line
of defence — not a replacement for proper secret management.

Enable/disable via env var ``PROMPT_SAFETY_ENABLED`` (default ``true``).
"""

from __future__ import annotations

import os
import re
from typing import Tuple, Optional

_TRUTHY = {"1", "true", "yes", "on"}
_ENABLED: bool = os.getenv("PROMPT_SAFETY_ENABLED", "true").strip().lower() in _TRUTHY

# ---------------------------------------------------------------------------
# Pattern categories — each tuple is (compiled_regex, reason_code)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_EXTRACTION: list[Tuple[re.Pattern, str]] = [
    (re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.I), "system_prompt_extract"),
    (re.compile(r"repeat\s+(your\s+)?(system\s+)?prompt", re.I), "system_prompt_extract"),
    (re.compile(r"what\s+are\s+your\s+(system\s+)?instructions", re.I), "system_prompt_extract"),
    (re.compile(r"show\s+(me\s+)?(your\s+)?(system\s+)?prompt", re.I), "system_prompt_extract"),
    (re.compile(r"print\s+(your\s+)?(system\s+)?prompt", re.I), "system_prompt_extract"),
    (re.compile(r"output\s+the\s+(system\s+)?prompt", re.I), "system_prompt_extract"),
    (re.compile(r"disregard\s+(all\s+)?prior\s+(instructions|rules)", re.I), "system_prompt_extract"),
    (re.compile(r"forget\s+(all\s+)?previous\s+(instructions|context)", re.I), "system_prompt_extract"),
]

_SECRET_EXFIL: list[Tuple[re.Pattern, str]] = [
    (re.compile(r"(print|show|output|display|cat|read|echo)\s+(me\s+)?(the\s+)?(contents?\s+of\s+)?\.env", re.I), "secret_exfil"),
    (re.compile(r"(print|show|output|display|cat|read|echo)\s+(me\s+)?(the\s+)?api[_\s]?key", re.I), "secret_exfil"),
    (re.compile(r"(print|show|output|display|cat|read|echo)\s+(me\s+)?(the\s+)?secret", re.I), "secret_exfil"),
    (re.compile(r"(print|show|output|display|cat|read|echo)\s+(me\s+)?(the\s+)?password", re.I), "secret_exfil"),
    (re.compile(r"(print|show|output|display|cat|read|echo)\s+(me\s+)?(the\s+)?private[_\s]?key", re.I), "secret_exfil"),
    (re.compile(r"cat\s+/etc/(passwd|shadow)", re.I), "secret_exfil"),
    (re.compile(r"(read|cat|print|show).{0,30}\.pem\b", re.I), "secret_exfil"),
    (re.compile(r"OPENAI_API_KEY", re.I), "secret_exfil"),
    (re.compile(r"GITHUB_TOKEN", re.I), "secret_exfil"),
]

_JAILBREAK: list[Tuple[re.Pattern, str]] = [
    (re.compile(r"you\s+are\s+now\s+(in\s+)?(DAN|developer|unrestricted)\s+mode", re.I), "jailbreak"),
    (re.compile(r"act\s+as\s+an?\s+(unrestricted|unfiltered|evil)\s+", re.I), "jailbreak"),
    (re.compile(r"pretend\s+(you\s+)?(are|have)\s+no\s+(rules|restrictions|limits)", re.I), "jailbreak"),
    (re.compile(r"do\s+anything\s+now", re.I), "jailbreak"),
]

_ALL_PATTERNS: list[Tuple[re.Pattern, str]] = (
    _SYSTEM_PROMPT_EXTRACTION + _SECRET_EXFIL + _JAILBREAK
)


def check_prompt_safety(text: str) -> Tuple[bool, Optional[str]]:
    """Check *text* for known unsafe prompt patterns.

    Returns ``(True, None)`` if the text is safe, or
    ``(False, reason_code)`` if a block pattern matched.

    When ``PROMPT_SAFETY_ENABLED`` is ``false`` (env var), always returns safe.
    """
    if not _ENABLED:
        return True, None

    if not text:
        return True, None

    for pattern, reason in _ALL_PATTERNS:
        if pattern.search(text):
            return False, reason

    return True, None


def assert_prompt_safe(text: str, *, context: str = "") -> None:
    """Raise ``ValueError`` if *text* fails prompt-safety checks.

    *context* is an optional label for log/error messages (e.g. ``"chat_input"``).
    """
    safe, reason = check_prompt_safety(text)
    if not safe:
        label = f" [{context}]" if context else ""
        raise ValueError(
            f"Prompt blocked by safety filter{label}: {reason}"
        )
