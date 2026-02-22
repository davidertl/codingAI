import os
import threading
import time
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import main
from github.issue_manager import get_ai_issues
from llm.provider import ensure_llm_ready, get_llm_runtime_status

SERVICE_POLL_INTERVAL_SECONDS = int(os.getenv("SERVICE_POLL_INTERVAL_SECONDS", str(main.POLL_INTERVAL)))
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def _utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_day_key():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _utc_week_key():
    now = datetime.now(timezone.utc)
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


class RepoWorker:
    def __init__(self, repo: str, poll_interval_seconds: int):
        self.repo = repo
        self.poll_interval_seconds = max(1, int(poll_interval_seconds))
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"codingai-worker-{repo}", daemon=True)
        self._lock = threading.Lock()
        self.started_at = _utc_now_iso()
        self.last_cycle_started_at = None
        self.last_cycle_finished_at = None
        self.last_error = ""
        self.cycles = 0

    def start(self):
        self._thread.start()

    def stop(self, timeout: float = 10.0):
        self._stop_event.set()
        self._thread.join(timeout=timeout)

    def is_running(self) -> bool:
        return self._thread.is_alive()

    def _run(self):
        while not self._stop_event.is_set():
            with self._lock:
                self.last_cycle_started_at = _utc_now_iso()

            try:
                main.run_repo_cycle_once(self.repo)
                err = ""
            except Exception as e:
                err = str(e)

            with self._lock:
                self.cycles += 1
                self.last_cycle_finished_at = _utc_now_iso()
                self.last_error = err[:1000]

            if self._stop_event.wait(self.poll_interval_seconds):
                break

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "repo": self.repo,
                "running": self.is_running(),
                "started_at": self.started_at,
                "last_cycle_started_at": self.last_cycle_started_at,
                "last_cycle_finished_at": self.last_cycle_finished_at,
                "last_error": self.last_error,
                "cycles": self.cycles,
                "poll_interval_seconds": self.poll_interval_seconds,
            }


class WorkerManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._workers: dict[str, RepoWorker] = {}

    def _available_repos(self) -> list[str]:
        return main.get_available_repos()

    def start_repo(self, repo: str) -> dict:
        if repo not in self._available_repos():
            raise KeyError(repo)

        with self._lock:
            existing = self._workers.get(repo)
            if existing and existing.is_running():
                snap = existing.snapshot()
                snap["already_running"] = True
                return snap

            worker = RepoWorker(repo=repo, poll_interval_seconds=SERVICE_POLL_INTERVAL_SECONDS)
            self._workers[repo] = worker
            worker.start()
            snap = worker.snapshot()
            snap["already_running"] = False
            return snap

    def stop_repo(self, repo: str) -> dict:
        with self._lock:
            worker = self._workers.get(repo)
            if not worker:
                return {"repo": repo, "running": False, "stopped": False, "reason": "not_running"}

        worker.stop(timeout=10.0)
        with self._lock:
            self._workers.pop(repo, None)

        snap = worker.snapshot()
        snap["stopped"] = True
        return snap

    def list_workers(self) -> list[dict]:
        with self._lock:
            workers = list(self._workers.values())
        return [w.snapshot() for w in workers]

    def is_repo_running(self, repo: str) -> bool:
        with self._lock:
            w = self._workers.get(repo)
            return bool(w and w.is_running())

    def stop_all(self):
        with self._lock:
            repos = list(self._workers.keys())
        for repo in repos:
            self.stop_repo(repo)


manager = WorkerManager()
app = FastAPI(title="CodingAI Control Plane", version="0.2.0")
app.mount("/ui/static", StaticFiles(directory=STATIC_DIR), name="ui-static")


def _require_repo(repo: str):
    repos = main.get_available_repos()
    if repo not in repos:
        raise HTTPException(status_code=404, detail=f"Unknown repo '{repo}'. Available: {repos}")


def _repo_state_summary(repo: str) -> list[dict]:
    state = main.load_state()
    repo_state = state.get(repo, {})
    out = []
    for key, value in repo_state.items():
        if not (isinstance(key, str) and key.isdigit() and isinstance(value, dict)):
            continue
        out.append(
            {
                "issue_number": int(key),
                "last_status": value.get("last_status"),
                "pr_created": bool(value.get("pr_created")),
                "pr_number": value.get("pr_number"),
                "pr_url": value.get("pr_url"),
                "check_run_url": value.get("check_run_url"),
                "report_comment_id": value.get("report_comment_id"),
                "patch_ops_count": value.get("patch_ops_count"),
                "patch_confidence": value.get("patch_confidence"),
                "strategy_confidence_threshold": value.get("strategy_confidence_threshold"),
                "retry_after": value.get("retry_after", 0),
                "pending_manual_approval": bool(value.get("pending_manual_approval")),
                "ai_stopped": bool(value.get("ai_stopped")),
                "ai_stop_reason": value.get("ai_stop_reason"),
                "active_branch": value.get("active_branch"),
                "last_error": value.get("last_error"),
                "source_issue_updated_at": value.get("source_issue_updated_at"),
            }
        )
    out.sort(key=lambda x: x["issue_number"])
    return out


def _repo_budget_status(repo: str) -> dict:
    state = main.load_state()
    policy = main.get_repo_policy_config(repo, force=False)
    day_key = _utc_day_key()
    week_key = _utc_week_key()
    daily_count = int(state.get("daily_pr_counts", {}).get(repo, {}).get(day_key, 0))
    weekly_count = int(state.get("weekly_pr_counts", {}).get(repo, {}).get(week_key, 0))

    max_day = int(policy.get("pr", {}).get("max_per_day", 0))
    max_week = int(policy.get("pr", {}).get("max_per_week", 0))
    return {
        "day_key": day_key,
        "week_key": week_key,
        "daily_count": daily_count,
        "weekly_count": weekly_count,
        "max_per_day": max_day,
        "max_per_week": max_week,
        "daily_remaining": None if max_day <= 0 else max(0, max_day - daily_count),
        "weekly_remaining": None if max_week <= 0 else max(0, max_week - weekly_count),
    }


@app.get("/health")
def health():
    llm_ready = ensure_llm_ready(force=False)
    llm_runtime = get_llm_runtime_status()
    return {
        "status": "ok",
        "time_utc": _utc_now_iso(),
        "workers_running": len([w for w in manager.list_workers() if w.get("running")]),
        "llm_ready": bool(llm_ready.get("ready")),
        "llm_active_provider": llm_ready.get("active_provider"),
        "llm_requested_provider": llm_ready.get("requested_provider"),
        "llm_provider_chain": llm_ready.get("provider_chain", []),
        "llm_telemetry_file": llm_runtime.get("telemetry_file"),
    }


@app.get("/", include_in_schema=False)
def ui_root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/ui", include_in_schema=False)
def ui_alias():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/repos")
def repos():
    available = main.get_available_repos()
    return [
        {
            "repo": repo,
            "running": manager.is_repo_running(repo),
        }
        for repo in available
    ]


@app.get("/policies")
def policies(force: bool = False):
    snapshot = main.get_policies_config(force=force)
    return {
        "time_utc": _utc_now_iso(),
        "available_repos": main.get_available_repos(),
        "policies": snapshot,
    }


@app.get("/policy/{repo}")
def repo_policy(repo: str, force: bool = False):
    _require_repo(repo)
    policy = main.get_repo_policy_config(repo, force=force)
    return {
        "repo": repo,
        "time_utc": _utc_now_iso(),
        "policy": policy,
        "budget": _repo_budget_status(repo),
    }


@app.get("/queue/{repo}")
def queue(repo: str):
    _require_repo(repo)
    try:
        issues = get_ai_issues(repo)
        queue_items = []
        for issue in issues:
            queue_items.append(
                {
                    "number": issue.get("number"),
                    "title": issue.get("title"),
                    "html_url": issue.get("html_url"),
                    "updated_at": issue.get("updated_at"),
                    "labels": [
                        l.get("name")
                        for l in issue.get("labels", [])
                        if isinstance(l, dict) and l.get("name")
                    ],
                }
            )
        return {"repo": repo, "count": len(queue_items), "issues": queue_items, "error": ""}
    except Exception as e:
        return {"repo": repo, "count": 0, "issues": [], "error": str(e)[:500]}


@app.get("/repo/{repo}/summary")
def repo_summary(repo: str):
    _require_repo(repo)
    queue_data = queue(repo)
    workers = manager.list_workers()
    worker = next((w for w in workers if w.get("repo") == repo), None)
    policy = main.get_repo_policy_config(repo, force=False)
    return {
        "repo": repo,
        "time_utc": _utc_now_iso(),
        "worker": worker,
        "queue": queue_data,
        "tracked_issues": _repo_state_summary(repo),
        "policy": policy,
        "budget": _repo_budget_status(repo),
    }


@app.get("/state")
def state():
    return {
        "time_utc": _utc_now_iso(),
        "available_repos": main.get_available_repos(),
        "workers": manager.list_workers(),
        "state": main.load_state(),
    }


@app.get("/workers")
def workers():
    return {
        "workers": manager.list_workers(),
    }


@app.post("/run/repo/{repo}")
def run_repo(repo: str):
    _require_repo(repo)
    try:
        snapshot = manager.start_repo(repo)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown repo '{repo}'")

    return {
        "repo": repo,
        "running": True,
        "already_running": bool(snapshot.get("already_running")),
        "worker": snapshot,
    }


@app.post("/run-once/repo/{repo}")
def run_repo_once(repo: str):
    _require_repo(repo)
    started = time.time()
    try:
        main.run_repo_cycle_once(repo)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:500])
    duration_ms = int((time.time() - started) * 1000)
    return {
        "repo": repo,
        "ran_once": True,
        "duration_ms": duration_ms,
    }


@app.post("/stop/repo/{repo}")
def stop_repo(repo: str):
    _require_repo(repo)
    snapshot = manager.stop_repo(repo)
    return {
        "repo": repo,
        "running": False,
        "stopped": bool(snapshot.get("stopped")),
        "worker": snapshot,
    }


@app.on_event("shutdown")
def _shutdown():
    manager.stop_all()
