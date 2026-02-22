import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone

from paths import LOGS_DIR

_LOCK = threading.Lock()
_COUNTERS = {}
_GAUGES = {}
_SUMMARIES = {}

_TRUTHY = {"1", "true", "yes", "on"}
EVENT_LOG_ENABLED = os.getenv("OBS_EVENT_LOG_ENABLED", "true").strip().lower() in _TRUTHY
EVENT_LOG_FILE = os.getenv(
    "OBS_EVENT_LOG_FILE",
    str((LOGS_DIR / "events.jsonl").resolve()),
).strip()


def _utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_labels(labels):
    if not isinstance(labels, dict):
        return {}
    out = {}
    for key, value in labels.items():
        k = str(key).strip()
        if not k:
            continue
        out[k] = str(value)
    return out


def _metric_key(name, labels):
    labels = _normalize_labels(labels)
    return name, tuple(sorted(labels.items()))


def _labels_from_key(label_items):
    return {k: v for k, v in label_items}


def _ensure_parent_dir(path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def inc_counter(name, value=1.0, labels=None):
    key = _metric_key(name, labels)
    with _LOCK:
        _COUNTERS[key] = float(_COUNTERS.get(key, 0.0)) + float(value)


def set_gauge(name, value, labels=None):
    key = _metric_key(name, labels)
    with _LOCK:
        _GAUGES[key] = float(value)


def observe_duration_ms(name, duration_ms, labels=None):
    key = _metric_key(name, labels)
    with _LOCK:
        stats = _SUMMARIES.get(key)
        if not stats:
            stats = {"sum": 0.0, "count": 0}
            _SUMMARIES[key] = stats
        stats["sum"] += float(duration_ms)
        stats["count"] += 1


def _safe_jsonable(value):
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def record_event(event, *, repo=None, issue_number=None, status=None, duration_ms=None, data=None):
    entry = {
        "ts": int(time.time()),
        "time_utc": _utc_now_iso(),
        "event": str(event),
    }
    if repo is not None:
        entry["repo"] = str(repo)
    if issue_number is not None:
        try:
            entry["issue_number"] = int(issue_number)
        except Exception:
            entry["issue_number"] = str(issue_number)
    if status is not None:
        entry["status"] = str(status)
    if duration_ms is not None:
        entry["duration_ms"] = int(max(0, duration_ms))
    if data is not None:
        entry["data"] = _safe_jsonable(data)

    inc_counter(
        "codingai_events_total",
        labels={
            "event": entry.get("event", "unknown"),
            "status": entry.get("status", "n/a"),
        },
    )
    if duration_ms is not None:
        observe_duration_ms(
            "codingai_event_duration_ms",
            duration_ms,
            labels={"event": entry.get("event", "unknown")},
        )

    if not EVENT_LOG_ENABLED:
        return entry

    try:
        _ensure_parent_dir(EVENT_LOG_FILE)
        with open(EVENT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=True, default=str) + "\n")
    except Exception:
        pass
    return entry


def _prom_escape(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_labels(labels):
    labels = _normalize_labels(labels)
    if not labels:
        return ""
    parts = [f'{k}="{_prom_escape(v)}"' for k, v in sorted(labels.items())]
    return "{" + ",".join(parts) + "}"


def get_metrics_snapshot():
    with _LOCK:
        counters = {
            f"{name}|{dict(label_items)}": value
            for (name, label_items), value in _COUNTERS.items()
        }
        gauges = {
            f"{name}|{dict(label_items)}": value
            for (name, label_items), value in _GAUGES.items()
        }
        summaries = {
            f"{name}|{dict(label_items)}": {"sum": stats["sum"], "count": stats["count"]}
            for (name, label_items), stats in _SUMMARIES.items()
        }
    return {
        "event_log_enabled": EVENT_LOG_ENABLED,
        "event_log_file": EVENT_LOG_FILE,
        "counters": counters,
        "gauges": gauges,
        "summaries": summaries,
    }


def render_prometheus_metrics():
    lines = []
    lines.append("# HELP codingai_events_total Total number of structured events emitted by CodingAI.")
    lines.append("# TYPE codingai_events_total counter")
    lines.append("# HELP codingai_event_duration_ms_duration Event duration observations in milliseconds.")
    lines.append("# TYPE codingai_event_duration_ms_duration summary")

    with _LOCK:
        counter_items = list(_COUNTERS.items())
        gauge_items = list(_GAUGES.items())
        summary_items = list(_SUMMARIES.items())

    for (name, label_items), value in sorted(counter_items, key=lambda x: (x[0][0], x[0][1])):
        labels = _labels_from_key(label_items)
        lines.append(f"{name}{_format_labels(labels)} {value}")

    for (name, label_items), value in sorted(gauge_items, key=lambda x: (x[0][0], x[0][1])):
        labels = _labels_from_key(label_items)
        lines.append(f"{name}{_format_labels(labels)} {value}")

    for (name, label_items), stats in sorted(summary_items, key=lambda x: (x[0][0], x[0][1])):
        labels = _labels_from_key(label_items)
        lines.append(f"{name}_sum{_format_labels(labels)} {stats['sum']}")
        lines.append(f"{name}_count{_format_labels(labels)} {stats['count']}")

    return "\n".join(lines) + "\n"


def read_recent_events(limit=100, repo=None, event=None):
    if limit <= 0:
        return []
    if not os.path.exists(EVENT_LOG_FILE):
        return []

    out = deque(maxlen=int(limit))
    repo = str(repo) if repo is not None else None
    event = str(event) if event is not None else None

    try:
        with open(EVENT_LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if repo is not None and str(item.get("repo")) != repo:
                    continue
                if event is not None and str(item.get("event")) != event:
                    continue
                out.append(item)
    except Exception:
        return []

    return list(out)
