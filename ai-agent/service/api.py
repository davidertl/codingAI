import os
import threading
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException

import main
from llm.provider import ensure_llm_ready, get_llm_runtime_status

SERVICE_POLL_INTERVAL_SECONDS = int(os.getenv("SERVICE_POLL_INTERVAL_SECONDS", str(main.POLL_INTERVAL)))


def _utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
app = FastAPI(title="CodingAI Control Plane", version="0.1.0")


def _require_repo(repo: str):
    repos = main.get_available_repos()
    if repo not in repos:
        raise HTTPException(status_code=404, detail=f"Unknown repo '{repo}'. Available: {repos}")


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
