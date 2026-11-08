"""Profile-owned task, call, transcript and event store (SQLite, standard library).

One file per profile under ``<hermes home>/plugin-data/hermes-line/``. WAL mode
lets a Desktop REST reader in another process read while the call service
writes. Every mutation bumps one global revision; each call keeps its last
revision and its last *control* revision (state or mode change), which is what
stale-action checks compare against.

Only masked text reaches this module: callers pass transcript and summary text
through ``engine.redact.mask_text`` first, and ``append_transcript`` masks again
as a second line of defence.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from ..engine.contracts import (PHASES, STATE_PHASE, CallRecord, CallState, Direction, Mode, Origin, TaskRecord,
                                TaskStatus)
from ..engine.redact import mask_text

SCHEMA_VERSION = 1
TRANSCRIPT_LIMIT = 400

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, goal TEXT NOT NULL,
  destination_e164 TEXT NOT NULL, destination_masked TEXT NOT NULL,
  mode TEXT NOT NULL, status TEXT NOT NULL, origin TEXT NOT NULL,
  created_at REAL NOT NULL, updated_at REAL NOT NULL,
  outcome TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
  allow_agent_fallback INTEGER NOT NULL DEFAULT 1, leave_voicemail INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS calls (
  id TEXT PRIMARY KEY, task_id TEXT, direction TEXT NOT NULL,
  counterpart_e164 TEXT NOT NULL, counterpart_masked TEXT NOT NULL,
  state TEXT NOT NULL, mode TEXT NOT NULL, created_at REAL NOT NULL,
  started_at REAL, ended_at REAL, provider_sid TEXT NOT NULL DEFAULT '',
  owner_sid TEXT NOT NULL DEFAULT '', conference TEXT NOT NULL DEFAULT '',
  end_reason TEXT NOT NULL DEFAULT '', summary TEXT,
  revision INTEGER NOT NULL DEFAULT 0, control_revision INTEGER NOT NULL DEFAULT 0,
  line_key TEXT NOT NULL DEFAULT 'default', state_since REAL,
  transport TEXT NOT NULL DEFAULT 'conversation_relay',
  phases TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS calls_sid ON calls(provider_sid);
CREATE INDEX IF NOT EXISTS calls_state ON calls(state);
CREATE TABLE IF NOT EXISTS transcript (
  call_id TEXT NOT NULL, seq INTEGER NOT NULL, speaker TEXT NOT NULL,
  text TEXT NOT NULL, ts REAL NOT NULL, PRIMARY KEY (call_id, seq)
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, call_id TEXT, task_id TEXT,
  kind TEXT NOT NULL, data TEXT NOT NULL, ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS relay_tokens (
  token_hash TEXT PRIMARY KEY, call_id TEXT NOT NULL, expires_at REAL NOT NULL,
  used INTEGER NOT NULL DEFAULT 0, kind TEXT NOT NULL DEFAULT 'relay'
);
CREATE TABLE IF NOT EXISTS commands (
  id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL, call_id TEXT, task_id TEXT,
  action TEXT NOT NULL, payload TEXT NOT NULL, source TEXT NOT NULL,
  status TEXT NOT NULL, result TEXT, created_at REAL NOT NULL, done_at REAL
);
CREATE TABLE IF NOT EXISTS outbox (
  id TEXT PRIMARY KEY, task_id TEXT, call_id TEXT, kind TEXT NOT NULL,
  text TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL, delivered_at REAL, last_error TEXT NOT NULL DEFAULT '',
  UNIQUE (call_id, kind)
);
CREATE TABLE IF NOT EXISTS inbound_admissions (
  sid TEXT PRIMARY KEY, call_id TEXT, twiml TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS owner_auth (
  line_key TEXT PRIMARY KEY, streak INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0,
  hard_locked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS owner_auth_failures (line_key TEXT NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS task_options (task_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS otp_pending (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, generation INTEGER NOT NULL, kind TEXT NOT NULL,
  call_id TEXT NOT NULL DEFAULT '', counterpart TEXT NOT NULL DEFAULT '', origin TEXT NOT NULL DEFAULT '',
  senders TEXT NOT NULL, window_start_ms INTEGER NOT NULL, window_end_ms INTEGER NOT NULL,
  prompt TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL, approval_generation INTEGER,
  UNIQUE (task_id, generation)
);
CREATE TABLE IF NOT EXISTS agent_sessions (
  agent_session_id TEXT PRIMARY KEY, call_id TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS lease (
  line_key TEXT PRIMARY KEY, owner_id TEXT NOT NULL, pid INTEGER NOT NULL,
  status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
  acquired_at REAL NOT NULL, heartbeat_at REAL NOT NULL
);
"""


class StaleRevision(Exception):
    def __init__(self, current: int):
        super().__init__(f"stale revision; current control revision is {current}")
        self.current = current


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class Store:
    def __init__(self, path: Path | str, clock=time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False,
                                     isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass
            self._conn.execute("PRAGMA busy_timeout=10000")
            self._conn.executescript(_SCHEMA)
            self._conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema', ?)", (str(SCHEMA_VERSION),))
            self._conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('revision', '0')")
            cols = {r[1] for r in self._conn.execute("PRAGMA table_info(calls)").fetchall()}
            if "state_since" not in cols:
                self._conn.execute("ALTER TABLE calls ADD COLUMN state_since REAL")
            if "transport" not in cols:
                self._conn.execute("ALTER TABLE calls ADD COLUMN transport TEXT NOT NULL DEFAULT 'conversation_relay'")
            if "phases" not in cols:  # round 37 (B9)
                self._conn.execute("ALTER TABLE calls ADD COLUMN phases TEXT NOT NULL DEFAULT '[]'")
            cols = {r[1] for r in self._conn.execute("PRAGMA table_info(otp_pending)").fetchall()}
            if "approval_generation" not in cols:
                self._conn.execute("ALTER TABLE otp_pending ADD COLUMN approval_generation INTEGER")
            cols = {r[1] for r in self._conn.execute("PRAGMA table_info(commands)").fetchall()}
            if "expected_revision" not in cols:
                self._conn.execute("ALTER TABLE commands ADD COLUMN expected_revision INTEGER")
            cols = {r[1] for r in self._conn.execute("PRAGMA table_info(relay_tokens)").fetchall()}
            if "kind" not in cols:
                self._conn.execute("ALTER TABLE relay_tokens ADD COLUMN kind TEXT NOT NULL DEFAULT 'relay'")

    # -- plumbing ---------------------------------------------------------
    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def _bump(self, conn: sqlite3.Connection) -> int:
        conn.execute("UPDATE meta SET value = CAST(value AS INTEGER) + 1 WHERE key = 'revision'")
        return int(conn.execute("SELECT value FROM meta WHERE key = 'revision'").fetchone()[0])

    def revision(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT value FROM meta WHERE key = 'revision'").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    # -- tasks ------------------------------------------------------------
    def create_task(self, *, title: str, goal: str, destination_e164: str, destination_masked: str,
                    mode: Mode, origin: Origin, allow_agent_fallback: bool = True,
                    leave_voicemail: bool = False) -> TaskRecord:
        now = self._clock()
        task = TaskRecord(id=new_id("task"), title=mask_text(title)[:120], goal=mask_text(goal)[:2000],
                          destination_e164=destination_e164, destination_masked=destination_masked,
                          mode=mode, status=TaskStatus.AWAITING_APPROVAL, origin=origin,
                          created_at=now, updated_at=now, allow_agent_fallback=allow_agent_fallback,
                          leave_voicemail=leave_voicemail)
        with self._tx() as c:
            c.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (task.id, task.title, task.goal, task.destination_e164, task.destination_masked,
                       task.mode.value, task.status.value, json.dumps(origin.to_dict()), now, now, "", "",
                       int(allow_agent_fallback), int(leave_voicemail)))
            self._bump(c)
        self.log_event(None, task.id, "task.created", {"mode": task.mode.value})
        return task

    def put_task_options(self, task_id: str, options: dict) -> None:
        """Round 16 (c): engine-enforced task options (owner_ring_timeout_s, fallback_question), set at creation."""
        with self._tx() as conn:
            conn.execute("INSERT OR REPLACE INTO task_options(task_id, data) VALUES (?, ?)",
                         (task_id, json.dumps(options, sort_keys=True)))

    def task_options(self, task_id: str) -> dict:
        with self._lock:
            row = self._conn.execute("SELECT data FROM task_options WHERE task_id = ?", (task_id,)).fetchone()
        return json.loads(row["data"]) if row else {}

    def get_task(self, task_id: str) -> Optional[TaskRecord]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _task(row) if row else None

    def update_task(self, task_id: str, **fields: Any) -> None:
        allowed = {"status", "outcome", "summary"}
        sets, args = [], []
        for key, value in fields.items():
            if key not in allowed:
                raise KeyError(key)
            if key in ("outcome", "summary"):
                value = mask_text(value or "")
            sets.append(f"{key} = ?")
            args.append(value.value if hasattr(value, "value") else value)
        if not sets:
            return
        with self._tx() as c:
            c.execute(f"UPDATE tasks SET {', '.join(sets)}, updated_at = ? WHERE id = ?",
                      (*args, self._clock(), task_id))
            self._bump(c)

    def list_tasks(self, *, open_only: bool = True, limit: int = 50) -> list[TaskRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit * 4,)).fetchall()
        tasks = [_task(r) for r in rows]
        if open_only:
            tasks = [t for t in tasks if t.status.open]
        return tasks[:limit]

    # -- calls ------------------------------------------------------------
    def create_call(self, *, task_id: Optional[str], direction: Direction, counterpart_e164: str,
                    counterpart_masked: str, state: CallState, mode: Mode, line_key: str = "default",
                    provider_sid: str = "", transport: str = "conversation_relay") -> CallRecord:
        now = self._clock()
        call = CallRecord(id=new_id("call"), task_id=task_id, direction=direction,
                          counterpart_e164=counterpart_e164, counterpart_masked=counterpart_masked,
                          state=state, mode=mode, created_at=now, provider_sid=provider_sid, line_key=line_key)
        first = STATE_PHASE.get(state)
        call.phases = [{"phase": first, "since": now}] if first else []
        with self._tx() as c:
            rev = self._bump(c)
            call.revision = call.control_revision = rev
            c.execute("INSERT INTO calls (id, task_id, direction, counterpart_e164, counterpart_masked, state, mode,"
                      " created_at, provider_sid, revision, control_revision, line_key, state_since, transport, phases)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (call.id, task_id, direction.value, counterpart_e164, counterpart_masked, state.value,
                       mode.value, now, provider_sid, rev, rev, line_key, now, transport, json.dumps(call.phases)))
        call.state_since = now
        return call

    def get_call(self, call_id: str) -> Optional[CallRecord]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
        return _call(row) if row else None

    def find_call_by_sid(self, sid: str) -> Optional[CallRecord]:
        if not sid:
            return None
        with self._lock:
            row = self._conn.execute("SELECT * FROM calls WHERE provider_sid = ? OR owner_sid = ?",
                                     (sid, sid)).fetchone()
        return _call(row) if row else None

    def update_call(self, call_id: str, *, control: bool = False, phase: Optional[str] = None, **fields: Any) -> int:
        """``phase`` (round 37, B9): with a ``state``, the phase it opens, when finer than STATE_PHASE's default. A new
        phase is appended to the call's phase list in the same transaction; a repeat of the current one is not."""
        allowed = {"state", "mode", "started_at", "ended_at", "provider_sid", "owner_sid", "conference",
                   "end_reason", "summary"}
        if phase is not None and phase not in PHASES:
            raise ValueError(phase)
        sets, args = [], []
        for key, value in fields.items():
            if key not in allowed:
                raise KeyError(key)
            if key == "summary" and value is not None:
                value = mask_text(value)
            sets.append(f"{key} = ?")
            args.append(value.value if hasattr(value, "value") else value)
        if "state" in fields or "mode" in fields:
            control = True
        opened = None
        if "state" in fields:
            now = self._clock()
            sets.append("state_since = ?")
            args.append(now)
            state = fields["state"]
            opened = phase or STATE_PHASE.get(state if isinstance(state, CallState) else CallState(state))
        with self._tx() as c:
            if opened is not None:
                row = c.execute("SELECT phases FROM calls WHERE id = ?", (call_id,)).fetchone()
                phases = json.loads(row["phases"] or "[]") if row else []
                if not phases or phases[-1]["phase"] != opened:
                    phases.append({"phase": opened, "since": now})
                    sets.append("phases = ?")
                    args.append(json.dumps(phases))
            rev = self._bump(c)
            sets.append("revision = ?")
            args.append(rev)
            if control:
                sets.append("control_revision = ?")
                args.append(rev)
            c.execute(f"UPDATE calls SET {', '.join(sets)} WHERE id = ?", (*args, call_id))
        return rev

    def live_calls(self, line_key: Optional[str] = None) -> list[CallRecord]:
        terminal = (CallState.ENDED.value, CallState.FAILED.value, CallState.AWAITING_APPROVAL.value)
        with self._lock:
            rows = self._conn.execute("SELECT * FROM calls WHERE state NOT IN (?,?,?)", terminal).fetchall()
        calls = [_call(r) for r in rows]
        return [c for c in calls if line_key is None or c.line_key == line_key]

    def list_calls(self, limit: int = 50) -> list[CallRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM calls ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [_call(r) for r in rows]

    def calls_for_task(self, task_id: str) -> list[CallRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM calls WHERE task_id = ? ORDER BY created_at",
                                      (task_id,)).fetchall()
        return [_call(r) for r in rows]

    # -- transcript and events -------------------------------------------
    def append_transcript(self, call_id: str, speaker: str, text: str) -> tuple[int, int]:
        clean = mask_text(text)[:2000]
        with self._tx() as c:
            row = c.execute("SELECT COALESCE(MAX(seq), 0) FROM transcript WHERE call_id = ?", (call_id,)).fetchone()
            seq = int(row[0]) + 1
            c.execute("INSERT INTO transcript VALUES (?,?,?,?,?)", (call_id, seq, speaker, clean, self._clock()))
            rev = self._bump(c)
            c.execute("UPDATE calls SET revision = ? WHERE id = ?", (rev, call_id))
        return seq, rev

    def destination_previously_approved(self, e164: str, exclude_task: Optional[str] = None) -> bool:
        """Round 16: was ``e164`` the destination of an earlier task the owner approved (it was dialled)?"""
        with self._lock:  # an outbound call to it was actually placed for another task (approved, then dialled)
            row = self._conn.execute(
                "SELECT 1 FROM calls WHERE counterpart_e164 = ? AND direction = 'outbound' AND provider_sid != '' "
                "AND COALESCE(task_id, '') != ? LIMIT 1", (e164, exclude_task or "")).fetchone()
        return row is not None

    def redact_transcript(self, call_id: str, seq: int, text: str) -> int:
        """Rewrite one transcript row (R1 delta 11 N14: a PIN fragment completed by a later frame). Returns the new
        call revision."""
        with self._tx() as c:
            c.execute("UPDATE transcript SET text = ? WHERE call_id = ? AND seq = ?", (mask_text(text)[:2000], call_id,
                                                                                    seq))
            rev = self._bump(c)
            c.execute("UPDATE calls SET revision = ? WHERE id = ?", (rev, call_id))
        return rev

    def transcript(self, call_id: str, limit: int = TRANSCRIPT_LIMIT) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT seq, speaker, text, ts FROM transcript WHERE call_id = ?"
                                      " ORDER BY seq DESC LIMIT ?", (call_id, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def log_event(self, call_id: Optional[str], task_id: Optional[str], kind: str, data: Optional[dict] = None) -> None:
        payload = json.dumps(_mask_values(data or {}), default=str)
        with self._lock:
            self._conn.execute("INSERT INTO events (call_id, task_id, kind, data, ts) VALUES (?,?,?,?,?)",
                               (call_id, task_id, kind, payload, self._clock()))

    def events(self, call_id: Optional[str] = None, kind: Optional[str] = None) -> list[dict]:
        q, args = "SELECT call_id, task_id, kind, data, ts FROM events", []
        conds = []
        if call_id:
            conds.append("call_id = ?")
            args.append(call_id)
        if kind:
            conds.append("kind = ?")
            args.append(kind)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        with self._lock:
            rows = self._conn.execute(q + " ORDER BY id", args).fetchall()
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows]

    # -- relay session tokens ---------------------------------------------
    def put_relay_token(self, token_hash: str, call_id: str, ttl_s: float, kind: str = "relay") -> None:
        with self._lock:
            self._conn.execute("INSERT INTO relay_tokens VALUES (?,?,?,0,?)",
                               (token_hash, call_id, self._clock() + ttl_s, kind))

    def take_relay_token(self, token_hash: str, kind: str = "relay") -> Optional[str]:
        with self._tx() as c:
            row = c.execute("SELECT call_id, expires_at, used, kind FROM relay_tokens WHERE token_hash = ?",
                            (token_hash,)).fetchone()
            if not row or row["used"] or row["expires_at"] < self._clock() or row["kind"] != kind:
                return None
            c.execute("UPDATE relay_tokens SET used = 1 WHERE token_hash = ?", (token_hash,))
            return row["call_id"]

    # -- owner commands (Desktop REST, slash commands) ----------------------
    def enqueue_command(self, *, idempotency_key: str, action: str, source: str, call_id: Optional[str] = None,
                        task_id: Optional[str] = None, payload: Optional[dict] = None,
                        expected_revision: Optional[int] = None) -> tuple[dict, bool]:
        """Insert a command once. Returns (row, created). A repeated idempotency key returns the
        original row unchanged. Raises StaleRevision when the call changed state since
        ``expected_revision``."""
        with self._tx() as c:
            row = c.execute("SELECT * FROM commands WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
            if row:
                return _command(row), False
            if call_id is not None and expected_revision is not None:
                cr = c.execute("SELECT control_revision FROM calls WHERE id = ?", (call_id,)).fetchone()
                if cr is None:
                    raise KeyError(call_id)
                if int(expected_revision) < int(cr[0]):
                    raise StaleRevision(int(cr[0]))
            cmd_id = new_id("cmd")
            c.execute("INSERT INTO commands (id, idempotency_key, call_id, task_id, action, payload, source, status,"
                      " result, created_at, done_at, expected_revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (cmd_id, idempotency_key, call_id, task_id, action, json.dumps(payload or {}), source,
                       "queued", None, self._clock(), None,
                       int(expected_revision) if expected_revision is not None else None))
            row = c.execute("SELECT * FROM commands WHERE id = ?", (cmd_id,)).fetchone()
            return _command(row), True

    def claim_commands(self, limit: int = 20) -> list[dict]:
        with self._tx() as c:
            rows = c.execute("SELECT * FROM commands WHERE status = 'queued' ORDER BY created_at LIMIT ?",
                             (limit,)).fetchall()
            for r in rows:
                c.execute("UPDATE commands SET status = 'running' WHERE id = ?", (r["id"],))
        return [_command(r) for r in rows]

    def finish_command(self, cmd_id: str, result: dict) -> None:
        with self._lock:
            self._conn.execute("UPDATE commands SET status = 'done', result = ?, done_at = ? WHERE id = ?",
                               (json.dumps(result), self._clock(), cmd_id))

    def pending_stop(self, call_id: str, task_id: Optional[str], acceptable) -> Optional[dict]:
        """R1 delta 5 N7: a stop for this call (``hang_up``) or task (``cancel``, ``deny``) that is submitted but
        not finished. The command row is written before ``submit`` returns, so this sees a stop from any process
        the moment it was asked for. N8: only a command the executor would run counts: ``acceptable(action,
        source)`` (the service's source rules) and, when it carries one, an ``expected_revision`` that is not stale."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM commands WHERE status IN ('queued', 'running') AND "
                "((action = 'hang_up' AND call_id = ?) OR (action IN ('cancel', 'deny') AND task_id = ?)) "
                "ORDER BY created_at", (call_id, task_id or "")).fetchall()
            revisions = {}
            for r in rows:
                if r["call_id"] and r["call_id"] not in revisions:
                    cr = self._conn.execute("SELECT control_revision FROM calls WHERE id = ?",
                                            (r["call_id"],)).fetchone()
                    revisions[r["call_id"]] = int(cr[0]) if cr else None
        for r in rows:
            cmd = _command(r)
            if not acceptable(cmd["action"], cmd["source"]):
                continue
            expected = cmd.get("expected_revision")
            if expected is not None and cmd.get("call_id"):
                current = revisions.get(cmd["call_id"])
                if current is None or int(expected) < current:
                    continue  # the executor would refuse it as stale
            return cmd
        return None

    def get_command(self, cmd_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM commands WHERE id = ?", (cmd_id,)).fetchone()
        return _command(row) if row else None

    def requeue_running_commands(self) -> int:
        with self._lock:
            return self._conn.execute("UPDATE commands SET status = 'queued' WHERE status = 'running'").rowcount

    # -- summary outbox ----------------------------------------------------
    # -- owner line PIN budget (R1 delta 10 N13) ------------------------------------------
    def owner_auth_locked(self, line_key: str, now: float) -> Optional[str]:
        """"hard" (until the owner unlocks), "cooldown" (until ``locked_until``) or None."""
        with self._lock:
            row = self._conn.execute("SELECT * FROM owner_auth WHERE line_key = ?", (line_key,)).fetchone()
        if row is None:
            return None
        if row["hard_locked"]:
            return "hard"
        return "cooldown" if now < float(row["locked_until"]) else None

    def owner_auth_failure(self, line_key: str, now: float, *, streak_limit: int, cooldown_s: float,
                           day_limit: int) -> Optional[str]:
        """Count one failed PIN for the line (not the caller). Returns "hard" or "cooldown" when this failure
        starts a lockout, else None. Persistent across calls and restarts."""
        with self._tx() as c:
            c.execute("INSERT OR IGNORE INTO owner_auth (line_key) VALUES (?)", (line_key,))
            c.execute("INSERT INTO owner_auth_failures (line_key, ts) VALUES (?, ?)", (line_key, now))
            c.execute("DELETE FROM owner_auth_failures WHERE line_key = ? AND ts < ?", (line_key, now - 86400))
            day = c.execute("SELECT COUNT(*) FROM owner_auth_failures WHERE line_key = ?", (line_key,)).fetchone()[0]
            row = c.execute("SELECT * FROM owner_auth WHERE line_key = ?", (line_key,)).fetchone()
            if row["hard_locked"]:
                return None
            if day >= day_limit:
                c.execute("UPDATE owner_auth SET hard_locked = 1, streak = 0 WHERE line_key = ?", (line_key,))
                return "hard"
            streak = int(row["streak"]) + 1
            if streak >= streak_limit:
                c.execute("UPDATE owner_auth SET streak = 0, locked_until = ? WHERE line_key = ?",
                          (now + cooldown_s, line_key))
                return "cooldown"
            c.execute("UPDATE owner_auth SET streak = ? WHERE line_key = ?", (streak, line_key))
            return None

    def owner_auth_success(self, line_key: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE owner_auth SET streak = 0 WHERE line_key = ?", (line_key,))

    def owner_auth_unlock(self, line_key: str) -> None:
        """The owner's ``/line unlock-owner``: clears the lockout and the 24 h failure count."""
        with self._tx() as c:
            c.execute("DELETE FROM owner_auth WHERE line_key = ?", (line_key,))
            c.execute("DELETE FROM owner_auth_failures WHERE line_key = ?", (line_key,))

    def outbox_put(self, *, task_id: Optional[str], call_id: str, kind: str, text: str) -> bool:
        with self._lock:
            cur = self._conn.execute("INSERT OR IGNORE INTO outbox (id, task_id, call_id, kind, text, status,"
                                     " created_at) VALUES (?,?,?,?,?,'pending',?)",
                                     (new_id("out"), task_id, call_id, kind, mask_text(text), self._clock()))
            return cur.rowcount == 1

    def outbox_pending(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM outbox WHERE status = 'pending' ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    def outbox_mark(self, out_id: str, *, delivered: bool, error: str = "") -> None:
        with self._lock:
            if delivered:
                self._conn.execute("UPDATE outbox SET status = 'delivered', delivered_at = ?, attempts = attempts + 1"
                                   " WHERE id = ?", (self._clock(), out_id))
            else:
                self._conn.execute("UPDATE outbox SET attempts = attempts + 1, last_error = ?,"
                                   " status = CASE WHEN attempts + 1 >= 5 THEN 'failed' ELSE 'pending' END"
                                   " WHERE id = ?", (error[:200], out_id))

    # -- inbound admissions: one admission per provider CallSid, ever (R1 H2) ------
    def get_admission(self, sid: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM inbound_admissions WHERE sid = ?", (sid,)).fetchone()
        return dict(row) if row else None

    def put_admission(self, sid: str, call_id: Optional[str], twiml: str) -> bool:
        """Record the first answer to a CallSid; False if the SID was already admitted."""
        with self._lock:
            try:
                self._conn.execute("INSERT INTO inbound_admissions VALUES (?,?,?,?)",
                                   (sid, call_id, twiml, self._clock()))
            except sqlite3.IntegrityError:
                return False
        return True

    # -- server-owned pending OTP approvals (R1 M1) ---------------------------------
    def create_otp_pending(self, *, task_id: str, kind: str, call_id: str, counterpart: str, origin: str,
                           senders: list, window_start_ms: int, window_end_ms: int, prompt: str) -> dict:
        with self._tx() as c:
            row = c.execute("SELECT COALESCE(MAX(generation), 0) FROM otp_pending WHERE task_id = ?",
                            (task_id,)).fetchone()
            generation = int(row[0]) + 1
            pid = new_id("otpp")
            c.execute("INSERT INTO otp_pending VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (pid, task_id, generation, kind, call_id, counterpart, origin, json.dumps(list(senders)),
                       int(window_start_ms), int(window_end_ms), prompt, "pending", self._clock(), None))
            out = c.execute("SELECT * FROM otp_pending WHERE id = ?", (pid,)).fetchone()
        return _pending(out)

    def get_otp_pending(self, pending_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM otp_pending WHERE id = ?", (pending_id,)).fetchone()
        return _pending(row) if row else None

    def take_otp_pending(self, pending_id: str, status: str) -> bool:
        """Move a pending approval out of ``pending`` exactly once (used / refused)."""
        with self._lock:
            cur = self._conn.execute("UPDATE otp_pending SET status = ? WHERE id = ? AND status = 'pending'",
                                     (status, pending_id))
        return cur.rowcount == 1

    def set_otp_approval_generation(self, pending_id: str, approval_generation: int) -> None:
        """The generation line.sms's registry returned for this approval (R1 M1)."""
        with self._lock:
            self._conn.execute("UPDATE otp_pending SET approval_generation = ? WHERE id = ?",
                               (int(approval_generation), pending_id))

    def max_used_otp_generation(self, task_id: str) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COALESCE(MAX(generation), 0) FROM otp_pending WHERE task_id = ?"
                                     " AND status = 'used'", (task_id,)).fetchone()
        return int(row[0])

    # -- Hermes sessions driving calls (in-call tools across processes) -------
    def put_agent_session(self, agent_session_id: str, call_id: str) -> None:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO agent_sessions VALUES (?,?,?)",
                               (agent_session_id, call_id, self._clock()))

    def get_agent_session(self, agent_session_id: str) -> Optional[str]:
        """The call a Hermes session drives, only while that call is live."""
        if not agent_session_id:
            return None
        with self._lock:
            row = self._conn.execute("SELECT a.call_id, c.state FROM agent_sessions a JOIN calls c ON c.id = a.call_id"
                                     " WHERE a.agent_session_id = ?", (agent_session_id,)).fetchone()
        if not row or row["state"] in (CallState.ENDED.value, CallState.FAILED.value):
            return None
        return row["call_id"]

    # -- lease rows (the lock itself is in engine.lease) --------------------
    def write_lease(self, line_key: str, owner_id: str, pid: int, status: str, detail: str = "") -> None:
        now = self._clock()
        with self._lock:
            self._conn.execute(
                "INSERT INTO lease VALUES (?,?,?,?,?,?,?) ON CONFLICT(line_key) DO UPDATE SET owner_id=excluded.owner_id,"
                " pid=excluded.pid, status=excluded.status, detail=excluded.detail,"
                " acquired_at=CASE WHEN lease.owner_id = excluded.owner_id THEN lease.acquired_at ELSE excluded.acquired_at END,"
                " heartbeat_at=excluded.heartbeat_at", (line_key, owner_id, pid, status, detail, now, now))

    def read_lease(self, line_key: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM lease WHERE line_key = ?", (line_key,)).fetchone()
        return dict(row) if row else None


def _mask_values(value: Any) -> Any:
    """Mask every string inside an event payload (masking the JSON text could break its syntax)."""
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, dict):
        return {str(k): _mask_values(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mask_values(v) for v in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return mask_text(str(value))


def _task(row: sqlite3.Row) -> TaskRecord:
    return TaskRecord(id=row["id"], title=row["title"], goal=row["goal"], destination_e164=row["destination_e164"],
                      destination_masked=row["destination_masked"], mode=Mode(row["mode"]),
                      status=TaskStatus(row["status"]), origin=Origin.from_dict(json.loads(row["origin"])),
                      created_at=row["created_at"], updated_at=row["updated_at"], outcome=row["outcome"],
                      summary=row["summary"], allow_agent_fallback=bool(row["allow_agent_fallback"]),
                      leave_voicemail=bool(row["leave_voicemail"]))


def _call(row: sqlite3.Row) -> CallRecord:
    return CallRecord(id=row["id"], task_id=row["task_id"], direction=Direction(row["direction"]),
                      counterpart_e164=row["counterpart_e164"], counterpart_masked=row["counterpart_masked"],
                      state=CallState(row["state"]), mode=Mode(row["mode"]), created_at=row["created_at"],
                      started_at=row["started_at"], ended_at=row["ended_at"], provider_sid=row["provider_sid"],
                      owner_sid=row["owner_sid"], conference=row["conference"], end_reason=row["end_reason"],
                      summary=row["summary"], revision=row["revision"], control_revision=row["control_revision"],
                      line_key=row["line_key"], state_since=row["state_since"], transport=row["transport"],
                      phases=json.loads(row["phases"] or "[]"))


def _pending(row: sqlite3.Row) -> dict:
    out = dict(row)
    out["senders"] = json.loads(out["senders"] or "[]")
    return out


def _command(row: sqlite3.Row) -> dict:
    out = dict(row)
    out["payload"] = json.loads(out["payload"] or "{}")
    out["result"] = json.loads(out["result"]) if out.get("result") else None
    return out
