#!/usr/bin/env python3
"""Mirror ONE call from a trial run's call store into the capture sandbox's store, read-only.

The source (a live trial's line.db) is opened with SQLite `mode=ro`: this process can never write
it, so pane controls clicked in the capture Desktop can never reach a live call (they enqueue into
the sandbox store, which no engine reads). Only the call's task, call and transcript rows are
copied; phone numbers are replaced by their masked form, so the sandbox holds no real number.
Transcript text is already masked by the engine's Store.

    python live_mirror.py --source <run>/home/plugin-data/hermes-line/line.db --dest <sandbox line.db>
                          [--call call_...] [--interval 0.5]

Prints one JSON line per change: {"event": "appeared"|"state"|"update", "call": ..., "state": ...,
"lines": n, "at": epoch}. Exits 0 when the source call has been terminal for --linger seconds.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from plugin.line.store.db import Store  # noqa: E402

TERMINAL = {"ended", "failed"}


def ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def read_source(path: Path, call_id: str | None):
    conn = ro(path)
    try:
        if call_id:
            call = conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
        else:
            call = conn.execute("SELECT * FROM calls ORDER BY created_at DESC LIMIT 1").fetchone()
        if call is None:
            return None
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (call["task_id"],)).fetchone() if call["task_id"] else None
        lines = conn.execute("SELECT * FROM transcript WHERE call_id = ? ORDER BY seq", (call["id"],)).fetchall()
        return dict(call), (dict(task) if task else None), [dict(r) for r in lines]
    finally:
        conn.close()


def write_dest(dest: Path, call: dict, task: dict | None, lines: list[dict]) -> None:
    conn = sqlite3.connect(str(dest), timeout=10, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        call = {**call, "counterpart_e164": call.get("counterpart_masked") or ""}
        if task is not None:
            task = {**task, "destination_e164": task.get("destination_masked") or ""}
        for table, rows, key in (("tasks", [task] if task else [], "id"), ("calls", [call], "id")):
            cols = [c for c in columns(conn, table) if rows and c in rows[0]]
            for row in rows:
                conn.execute(f"DELETE FROM {table} WHERE {key} = ?", (row[key],))
                conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                             [row[c] for c in cols])
        conn.execute("DELETE FROM transcript WHERE call_id = ?", (call["id"],))
        tcols = [c for c in columns(conn, "transcript") if lines and c in lines[0]]
        for row in lines:
            conn.execute(f"INSERT INTO transcript ({', '.join(tcols)}) VALUES ({', '.join('?' * len(tcols))})",
                         [row[c] for c in tcols])
        conn.execute("UPDATE meta SET value = CAST(value AS INTEGER) + 1 WHERE key = 'revision'")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def emit(event: str, call: dict, lines: list[dict]) -> None:
    print(json.dumps({"event": event, "call": call["id"], "state": call["state"], "lines": len(lines), "at": time.time()}), flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--dest", required=True)
    ap.add_argument("--call")
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--linger", type=float, default=8.0, help="seconds to keep mirroring after the call ends")
    args = ap.parse_args()

    source = Path(args.source).resolve()
    dest = Path(args.dest).resolve()
    if source == dest:
        sys.exit("source and destination must differ")
    Store(dest).close()  # create the sandbox schema the gateway's REST layer reads

    last_key = None
    last_state = None
    ended_at = None
    while True:
        if not source.exists():
            time.sleep(args.interval)
            continue
        try:
            got = read_source(source, args.call)
        except sqlite3.Error as exc:  # the writer is mid-transaction or the file is being created
            print(json.dumps({"event": "read_retry", "error": type(exc).__name__, "at": time.time()}), flush=True)
            time.sleep(args.interval)
            continue
        if got is not None:
            call, task, lines = got
            key = (call["id"], call["state"], call.get("revision"), len(lines), call.get("summary"))
            if key != last_key:
                write_dest(dest, call, task, lines)
                emit("appeared" if last_key is None else ("state" if call["state"] != last_state else "update"), call, lines)
                last_key, last_state = key, call["state"]
            if call["state"] in TERMINAL:
                ended_at = ended_at or time.time()
                if time.time() - ended_at >= args.linger:
                    return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
