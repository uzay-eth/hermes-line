"""Desktop REST for the pane (docs/CONTRACT.md, "Desktop REST and events").

Served by Hermes under ``/api/plugins/hermes-line/`` from a dashboard
``plugin_api.py`` that only does ``router = create_router()``. That file is
imported as a standalone module, possibly in a different process from the call
service, so this layer talks to the service only through the store: it reads
snapshots and enqueues owner commands (source ``desktop``), which the
line-owning process executes. Requests reaching this namespace are already
authenticated as the Desktop owner by Hermes.

Revisions: ``GET /calls`` returns the store's global revision. A POST must send
a ``revision`` at least as new as the call's last state or mode change;
otherwise 409, so a click made on an outdated view (the call already ended,
or a person already answered) is refused. Transcript lines alone never make
an action stale. ``idempotency_key`` makes a retried click a no-op.
"""
# No ``from __future__ import annotations``: FastAPI must resolve ``Request`` in create_router.
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from ..engine.contracts import CallState
from ..store import StaleRevision, Store

CALL_ACTIONS = {"take_over", "say", "hang_up"}
TASK_ACTIONS = {"approve", "deny"}
_KEY = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")


def iso(ts: Optional[float]) -> Optional[str]:
    """ISO 8601 UTC with offset, as docs/CONTRACT.md requires for every timestamp."""
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(timespec="milliseconds")


def call_view(store: Store, call, *, transcript_limit: int = 200) -> dict:
    task = store.get_task(call.task_id) if call.task_id else None
    end = call.ended_at or time.time()
    start = call.started_at or (call.created_at if call.state is not CallState.AWAITING_APPROVAL else None)
    return {
        "id": call.id,
        "task_id": call.task_id,
        "task_title": task.title if task else "Incoming call",
        # Readiness RC-4: the goal the call session gets, in the snapshot a task POST takes its revision from.
        "task_goal": task.goal if task else None,
        "direction": call.direction.value,
        "counterpart_masked": call.counterpart_masked,
        "state": call.state.value,
        "started_at": iso(start),
        "state_since": iso(call.state_since or call.created_at),
        "duration_s": int(max(0.0, end - start)) if start else 0,
        "mode": call.mode.value,
        "transcript": [{**t, "ts": iso(t["ts"])} for t in store.transcript(call.id, limit=transcript_limit)],
        "summary": call.summary,
        "end_reason": call.end_reason or None,
        "revision": call.revision,
        "control_revision": call.control_revision,
        # Round 37 (B9): the engine's phase history, oldest first; see docs/lanes/L1.md section 49 for L3.
        "phases": [{"phase": p["phase"], "since": iso(p["since"])} for p in (call.phases or [])],
    }


def snapshot(store: Store, limit: int = 50) -> dict:
    revision = store.revision()
    calls = store.list_calls(limit=limit)
    calls.sort(key=lambda c: (c.state.terminal, -c.created_at))
    return {"revision": revision, "calls": [call_view(store, c) for c in calls]}


class DesktopApi:
    def __init__(self, store_provider: Callable[[], Store], *, wait_s: float = 3.0):
        self._store = store_provider
        self.wait_s = wait_s

    def get_calls(self) -> tuple[int, dict]:
        return 200, snapshot(self._store())

    def get_status(self) -> tuple[int, dict]:
        store = self._store()
        rows = []
        try:
            rows = [store.read_lease("default")]
        except Exception:
            pass
        lease = next((r for r in rows if r), None)
        return 200, {"service": (lease or {}).get("status", "stopped"), "revision": store.revision()}

    def _validate(self, body: Any, *, need_revision: bool) -> Optional[tuple[int, dict]]:
        if not isinstance(body, dict):
            return 400, {"error": "body must be a JSON object"}
        key = body.get("idempotency_key")
        if not isinstance(key, str) or not _KEY.match(key):
            return 400, {"error": "idempotency_key is required (8-128 safe characters)"}
        rev = body.get("revision")
        if need_revision and (not isinstance(rev, int) or isinstance(rev, bool) or rev < 0):
            return 400, {"error": "revision is required"}
        return None

    def _wait(self, store: Store, cmd_id: str) -> Optional[dict]:
        deadline = time.monotonic() + self.wait_s
        while time.monotonic() < deadline:
            row = store.get_command(cmd_id)
            if row and row["status"] == "done":
                return row["result"]
            time.sleep(0.05)
        return None

    def _enqueue(self, store: Store, **kw) -> tuple[int, dict]:
        try:
            row, created = store.enqueue_command(source="desktop", **kw)
        except StaleRevision as exc:
            return 409, {"error": "stale_revision", "revision": store.revision(), "control_revision": exc.current}
        except KeyError:
            return 404, {"error": "unknown call"}
        result = row.get("result") if row["status"] == "done" else self._wait(store, row["id"])
        body = {"accepted": True, "command_id": row["id"], "duplicate": not created,
                "status": "done" if result is not None else "queued", "result": result,
                "revision": store.revision()}
        return (200 if result is not None else 202), body

    def post_call_action(self, call_id: str, action: str, body: Any) -> tuple[int, dict]:
        if action not in CALL_ACTIONS:
            return 404, {"error": "unknown action"}
        bad = self._validate(body, need_revision=True)
        if bad:
            return bad
        store = self._store()
        call = store.get_call(call_id)
        if call is None:
            return 404, {"error": "unknown call"}
        payload = {}
        if action == "say":
            text = body.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 500:
                return 400, {"error": "text is required (max 500 characters)"}
            payload["text"] = text.strip()
        if call.state.terminal:
            return 409, {"error": "call_ended", "revision": store.revision(), "control_revision": call.control_revision}
        return self._enqueue(store, idempotency_key=f"desktop:{body['idempotency_key']}", action=action,
                             call_id=call_id, task_id=call.task_id, payload=payload,
                             expected_revision=body["revision"])

    def post_task_action(self, task_id: str, action: str, body: Any) -> tuple[int, dict]:
        if action not in TASK_ACTIONS:
            return 404, {"error": "unknown action"}
        bad = self._validate(body, need_revision=True)
        if bad:
            return bad
        store = self._store()
        task = store.get_task(task_id)
        if task is None:
            return 404, {"error": "unknown task"}
        waiting = next((c for c in store.calls_for_task(task_id) if c.state is CallState.AWAITING_APPROVAL), None)
        if waiting is None:
            return 409, {"error": "not_awaiting_approval", "revision": store.revision()}
        return self._enqueue(store, idempotency_key=f"desktop:{body['idempotency_key']}", action=action,
                             call_id=waiting.id, task_id=task_id, expected_revision=body["revision"])


def default_store() -> Store:
    from plugins.plugin_storage import plugin_data_dir  # public plugin surface; follows the request profile
    return Store(plugin_data_dir("hermes-line") / "line.db")


def create_router(store_provider: Optional[Callable[[], Store]] = None):
    """FastAPI router for a dashboard ``plugin_api.py``: ``router = create_router()``."""
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool

    api = DesktopApi(store_provider or default_store)
    router = APIRouter()

    async def body_of(request: Request) -> Any:
        try:
            return await request.json()
        except Exception:
            return None

    @router.get("/calls")
    async def calls():
        status, body = await run_in_threadpool(api.get_calls)
        return JSONResponse(body, status_code=status)

    @router.get("/status")
    async def status():
        code, body = await run_in_threadpool(api.get_status)
        return JSONResponse(body, status_code=code)

    @router.post("/calls/{call_id}/{action}")
    async def call_action(call_id: str, action: str, request: Request):
        code, body = await run_in_threadpool(api.post_call_action, call_id, action, await body_of(request))
        return JSONResponse(body, status_code=code)

    @router.post("/tasks/{task_id}/{action}")
    async def task_action(task_id: str, action: str, request: Request):
        code, body = await run_in_threadpool(api.post_task_action, task_id, action, await body_of(request))
        return JSONResponse(body, status_code=code)

    return router
