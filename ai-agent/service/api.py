import os
import threading
import time
import hashlib
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core.observability import (
    get_metrics_snapshot,
    inc_counter,
    read_recent_events,
    record_event,
    render_prometheus_metrics,
    set_gauge,
    redact_dict,
)
from core.research import research
import main
from github.ci_status import get_pr_ci_status
from github.issue_manager import get_ai_issues
from llm.provider import ensure_llm_ready, get_llm_runtime_status
from paths import ENV_FILE, GITHUB_APP_PEM_FILE, setup_status
from github.app_auth import get_installation_token
from github.repo_manager import list_installation_repos

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
                inc_counter("codingai_worker_errors_total", labels={"repo": self.repo})
                record_event(
                    "worker_cycle_error",
                    repo=self.repo,
                    status="error",
                    data={"error": err[:300]},
                )

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
        self._auto_thread = threading.Thread(target=self._auto_scheduler, name="codingai-auto-scheduler", daemon=True)
        self._auto_thread.start()

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
        snapshots = [w.snapshot() for w in workers]
        set_gauge(
            "codingai_workers_running",
            len([w for w in snapshots if w.get("running")]),
        )
        return snapshots

    def is_repo_running(self, repo: str) -> bool:
        with self._lock:
            w = self._workers.get(repo)
            return bool(w and w.is_running())

    def stop_all(self):
        with self._lock:
            repos = list(self._workers.keys())
        for repo in repos:
            self.stop_repo(repo)

    def _auto_scheduler(self):
        while True:
            try:
                auto = main.load_state().get("automation", {}) or {}
                for repo, cfg in auto.items():
                    if not isinstance(cfg, dict):
                        continue
                    if not cfg.get("enabled"):
                        continue
                    if self.is_repo_running(repo):
                        continue
                    if repo not in self._available_repos():
                        continue
                    # start worker for automated repos
                    try:
                        self.start_repo(repo)
                        record_event("auto_start_worker", repo=repo, status="started")
                    except Exception as e:
                        record_event("auto_start_worker", repo=repo, status="error", data={"error": str(e)[:200]})
                # run periodic cleanup/self-check hooks
                from github.repo_manager import cleanup_jobs
                cleanup_jobs()
            except Exception:
                pass
            time.sleep(max(60, SERVICE_POLL_INTERVAL_SECONDS))


manager = WorkerManager()
app = FastAPI(title="CodingAI Control Plane", version="0.4.0")
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
                "test_patch_applied": bool(value.get("test_patch_applied")),
                "test_patch_ops_added": value.get("test_patch_ops_added"),
                "test_patch_confidence": value.get("test_patch_confidence"),
                "strategy_confidence_threshold": value.get("strategy_confidence_threshold"),
                "strategy_max_attempts": value.get("strategy_max_attempts"),
                "strategy_quarantine_threshold": value.get("strategy_quarantine_threshold"),
                "strategy_quarantine_seconds": value.get("strategy_quarantine_seconds"),
                "retry_after": value.get("retry_after", 0),
                "pending_manual_approval": bool(value.get("pending_manual_approval")),
                "ai_stopped": bool(value.get("ai_stopped")),
                "ai_stop_reason": value.get("ai_stop_reason"),
                "active_branch": value.get("active_branch"),
                "last_error": value.get("last_error"),
                "source_issue_updated_at": value.get("source_issue_updated_at"),
                "last_duration_ms": value.get("last_duration_ms"),
                "last_processed_at": value.get("last_processed_at"),
                "ci_gate": value.get("ci_gate"),
                "pipeline": value.get("pipeline", {}),
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
    set_gauge("codingai_workers_running", len([w for w in manager.list_workers() if w.get("running")]))
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


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    return render_prometheus_metrics()


@app.get("/setup/status")
def setup_status_api():
    return setup_status()


def _write_env_entries(entries: dict):
    existing = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    existing[k] = v
    existing.update(entries)
    lines = [f"{k}={v}\n" for k, v in existing.items()]
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines)


@app.post("/setup/github")
def setup_github(
    owner: str = Form(...),
    app_id: str = Form(...),
    installation_id: str = Form(...),
):
    owner = owner.strip()
    app_id = app_id.strip()
    installation_id = installation_id.strip()
    if not (owner and app_id and installation_id):
        raise HTTPException(status_code=400, detail="owner, app_id, installation_id are required")
    _write_env_entries(
        {
            "GITHUB_OWNER": owner,
            "GITHUB_APP_ID": app_id,
            "GITHUB_INSTALLATION_ID": installation_id,
        }
    )
    return {"status": "ok", "setup": setup_status()}


@app.post("/setup/pem")
async def setup_pem(pem: UploadFile = File(...)):
    content = await pem.read()
    if not content or len(content) > 20000:
        raise HTTPException(status_code=400, detail="PEM content invalid or too large")
    if b"BEGIN" not in content or b"PRIVATE KEY" not in content:
        raise HTTPException(status_code=400, detail="PEM format not recognized")
    os.makedirs(GITHUB_APP_PEM_FILE.parent, exist_ok=True)
    with open(GITHUB_APP_PEM_FILE, "wb") as f:
        f.write(content)
    os.chmod(GITHUB_APP_PEM_FILE, 0o600)
    fingerprint = hashlib.sha256(content).hexdigest()
    return {"status": "ok", "fingerprint": fingerprint, "setup": setup_status()}


@app.get("/projects")
def projects():
    state = main.load_state()
    enabled = set(state.get("projects_enabled", []))
    try:
        token = get_installation_token()
        repos = list_installation_repos(token)
    except Exception:
        repos = main.get_available_repos()
    projects = []
    for repo in repos:
        projects.append(
            {
                "repo": repo,
                "enabled": repo in enabled or not enabled,  # default enable all if none set
            }
        )
    return {
        "time_utc": _utc_now_iso(),
        "projects": projects,
    }


def _set_project_enabled(repo: str, enabled: bool):
    state = main.load_state()
    enabled_set = set(state.get("projects_enabled", []))
    if enabled:
        enabled_set.add(repo)
    else:
        enabled_set.discard(repo)
    state["projects_enabled"] = sorted(enabled_set)
    main.save_state(state)
    return {"repo": repo, "enabled": enabled, "projects_enabled": state["projects_enabled"]}


@app.post("/projects/{repo}/enable")
def enable_project(repo: str):
    return _set_project_enabled(repo, True)


@app.post("/projects/{repo}/disable")
def disable_project(repo: str):
    return _set_project_enabled(repo, False)


def _get_issue_state_or_404(repo: str, issue_number: int):
    state = main.load_state()
    repo_state = state.get(repo, {})
    issue_state = repo_state.get(str(issue_number))
    if not issue_state:
        raise HTTPException(status_code=404, detail=f"No state recorded for issue {issue_number} in {repo}")
    return state, issue_state


@app.get("/pipeline/{repo}/{issue_number}")
def pipeline(repo: str, issue_number: int):
    _require_repo(repo)
    state, issue_state = _get_issue_state_or_404(repo, issue_number)
    pipeline = issue_state.get("pipeline") or {}
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline state not available")
    return {
        "repo": repo,
        "issue": issue_number,
        "pipeline": pipeline,
        "time_utc": _utc_now_iso(),
    }


@app.get("/pipeline/{repo}/{issue_number}/diff")
def pipeline_diff(repo: str, issue_number: int):
    _require_repo(repo)
    _state, issue_state = _get_issue_state_or_404(repo, issue_number)
    pipeline = issue_state.get("pipeline") or {}
    diff = pipeline.get("diff", "")
    return {
        "repo": repo,
        "issue": issue_number,
        "diff": diff,
        "length": len(diff),
        "time_utc": _utc_now_iso(),
    }


def _pipeline_control(repo: str, issue_number: int, *, action: str, reason: str | None = None):
    state, issue_state = _get_issue_state_or_404(repo, issue_number)
    pipeline = issue_state.setdefault("pipeline", {})
    now = int(time.time())
    if action == "pause":
        pipeline["pause_requested"] = True
        pipeline["paused"] = True
        pipeline["paused_at"] = now
        pipeline.setdefault("pause_deadline", now + main.PAUSE_WINDOW_SECONDS)
    elif action == "resume":
        pipeline["pause_requested"] = False
        pipeline["paused"] = False
        pipeline["pause_deadline"] = now + 3
    elif action == "cancel":
        pipeline["cancel_requested"] = True
        if reason:
            pipeline["cancel_reason"] = reason[:200]
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action {action}")
    pipeline["updated_at"] = now
    main.save_state(state)
    return {"status": "ok", "pipeline": pipeline, "time_utc": _utc_now_iso()}


@app.post("/pipeline/{repo}/{issue_number}/pause")
def pipeline_pause(repo: str, issue_number: int):
    _require_repo(repo)
    return _pipeline_control(repo, issue_number, action="pause")


@app.post("/pipeline/{repo}/{issue_number}/resume")
def pipeline_resume(repo: str, issue_number: int):
    _require_repo(repo)
    return _pipeline_control(repo, issue_number, action="resume")


@app.post("/pipeline/{repo}/{issue_number}/cancel")
def pipeline_cancel(repo: str, issue_number: int, reason: str | None = None):
    _require_repo(repo)
    return _pipeline_control(repo, issue_number, action="cancel", reason=reason)


@app.get("/research")
def research_query(query: str, max_results: int | None = None, use_cache: bool = True):
    if not query or not query.strip():
        raise HTTPException(status_code=400, detail="query is required")
    try:
        data = research(query.strip(), max_results=max_results, use_cache=use_cache)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:400])
    return {
        "time_utc": _utc_now_iso(),
        "query": data.get("query"),
        "cached": data.get("cached", False),
        "results": data.get("results", []),
        "summary": data.get("summary", ""),
        "search_error": data.get("search_error"),
        "summary_error": data.get("summary_error"),
    }


@app.get("/metrics/json")
def metrics_json():
    return {
        "time_utc": _utc_now_iso(),
        "metrics": get_metrics_snapshot(),
    }


@app.post("/self-checks")
def run_self_checks():
    try:
        main.self_checks()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])
    return {"status": "ok", "time_utc": _utc_now_iso()}


@app.get("/events")
def events(limit: int = 100, repo: str | None = None, event: str | None = None):
    limit = max(1, min(int(limit), 1000))
    items = read_recent_events(limit=limit, repo=repo, event=event)
    items = [redact_dict(i) if isinstance(i, dict) else i for i in items]
    return {
        "time_utc": _utc_now_iso(),
        "count": len(items),
        "events": items,
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
    auto = main.load_state().get("automation", {})
    return [
        {
            "repo": repo,
            "running": manager.is_repo_running(repo),
            "automation": bool(auto.get(repo, {}).get("enabled", False)) if isinstance(auto, dict) else False,
        }
        for repo in available
    ]


def _automation_state():
    state = main.load_state()
    return state.setdefault("automation", {})


@app.get("/automation")
def automation():
    auto = _automation_state()
    return {"time_utc": _utc_now_iso(), "automation": auto}


def _set_automation(repo: str, enabled: bool, push_gate: str | None = None):
    state = main.load_state()
    auto = state.setdefault("automation", {})
    cfg = auto.get(repo, {}) if isinstance(auto.get(repo), dict) else {}
    cfg["enabled"] = enabled
    if push_gate:
        cfg["push_gate"] = push_gate
    cfg["updated_at"] = int(time.time())
    auto[repo] = cfg
    main.save_state(state)
    return cfg


@app.post("/automation/{repo}/enable")
def automation_enable(repo: str, push_gate: str | None = None):
    _require_repo(repo)
    cfg = _set_automation(repo, True, push_gate)
    return {"status": "ok", "repo": repo, "automation": cfg}


@app.post("/automation/{repo}/disable")
def automation_disable(repo: str):
    _require_repo(repo)
    cfg = _set_automation(repo, False)
    return {"status": "ok", "repo": repo, "automation": cfg}


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


@app.get("/ci/{repo}/{pr_number}")
def ci_status(repo: str, pr_number: int):
    _require_repo(repo)
    started = time.time()
    try:
        summary = get_pr_ci_status(repo, int(pr_number))
    except Exception as e:
        inc_counter("codingai_ci_gate_total", labels={"repo": repo, "result": "api_error"})
        raise HTTPException(status_code=502, detail=f"CI status lookup failed: {str(e)[:280]}")
    duration_ms = int((time.time() - started) * 1000)
    record_event(
        "api_ci_status_lookup",
        repo=repo,
        status="ok",
        duration_ms=duration_ms,
        data={"pr_number": int(pr_number)},
    )
    return {
        "repo": repo,
        "time_utc": _utc_now_iso(),
        "ci_status": summary,
        "duration_ms": duration_ms,
    }


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
    inc_counter("codingai_api_actions_total", labels={"action": "run_repo"})
    record_event("api_run_repo", repo=repo, status="started")

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
        duration_ms = int((time.time() - started) * 1000)
        inc_counter("codingai_api_actions_total", labels={"action": "run_repo_once_error"})
        record_event(
            "api_run_repo_once",
            repo=repo,
            status="error",
            duration_ms=duration_ms,
            data={"error": str(e)[:280]},
        )
        raise HTTPException(status_code=500, detail=str(e)[:500])
    duration_ms = int((time.time() - started) * 1000)
    inc_counter("codingai_api_actions_total", labels={"action": "run_repo_once"})
    record_event("api_run_repo_once", repo=repo, status="ok", duration_ms=duration_ms)
    return {
        "repo": repo,
        "ran_once": True,
        "duration_ms": duration_ms,
    }


@app.post("/stop/repo/{repo}")
def stop_repo(repo: str):
    _require_repo(repo)
    snapshot = manager.stop_repo(repo)
    inc_counter("codingai_api_actions_total", labels={"action": "stop_repo"})
    record_event("api_stop_repo", repo=repo, status="stopped")
    return {
        "repo": repo,
        "running": False,
        "stopped": bool(snapshot.get("stopped")),
        "worker": snapshot,
    }


@app.on_event("shutdown")
def _shutdown():
    manager.stop_all()
