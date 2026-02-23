import hashlib
import json
from datetime import datetime, timezone


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(data) -> str:
    return json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", errors="ignore")).hexdigest()


def fingerprint_text(value: str, length: int = 16) -> str:
    digest = sha256_text(value)
    length = max(6, min(int(length), len(digest)))
    return digest[:length]
