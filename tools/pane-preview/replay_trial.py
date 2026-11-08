#!/usr/bin/env python3
"""Replay a recorded trial call (evidence/trial/<call>/trial.json) into a scratch call store, in real time.

The store is written the way the engine writes it during a live call, through L1's Store API and
the real clock: the task, the call (dialing), each transcript line at its recorded offset, each
state change at its recorded offset, and the end with its summary. live-capture.mjs mirrors this
store exactly as it would mirror a live trial's store, so the replay proves the capture path
without a phone call.

    python replay_trial.py --evidence evidence/trial/call_.../trial.json --db <scratch>/line.db [--speed 1]

The store on main writes the call's phase list itself on every state change (L1 round 37); the
replay passes `phase="fallback"` where the engine does (agent_talking after the owner's ring timed
out), so the phase list is L1's own. Recorded calls from before round 37 carry the old summary;
`--summary round37` (default) recomposes it in the round-37 order from the evidence record: the outcome
sentence, the original log line, the engine's fallback note, and "On the call:" with the last 12
lines. The replay prints the composed summary and marks it as reconstructed.

Refuses to write anywhere except a fresh file under a path containing "scratch".
"""
from __future__ import annotations

import re

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from plugin.line.engine.contracts import CallState, Direction, Mode, Origin  # noqa: E402
from plugin.line.store.db import Store  # noqa: E402


def iso(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


def round37_summary(call: dict, events: list) -> str:
    """L1 round 37's summary order (docs/lanes/L1.md section 49), rebuilt from a recorded call: the outcome
    sentence for the recorded path, the original log line, the engine's own fallback note, and the last
    12 lines actually exchanged."""
    old = call.get("summary") or ""
    m = re.match(r'(Hermes Line: .*?\(.*?\)\.)\s*(.*)$', old, re.S)
    log, note = (m.group(1), m.group(2).strip()) if m else ("", old)
    owner_m = re.search(r"(\w+) did not take the call", old)
    owner = owner_m.group(1) if owner_m else "the owner"
    kinds = {e["kind"] for e in events}
    states = {e["data"].get("to") for e in events if e["kind"] == "state"}
    secs = int(call.get("duration_s") or 0)
    took = f"{secs} s" if secs < 60 else f"{secs // 60} min {secs % 60} s"
    if "owner_connected" in states:
        sentence = f"A person answered and {owner} was connected; the call ended after {took}."
    elif "owner_ring_timeout" in kinds and "fallback_question_answered" in kinds:
        # L1 round 38 wording (machine._outcome_sentence).
        sentence = (f"{owner} did not pick up, so Hermes asked the fallback question and got a reply (not verified); "
                    f"the call ended after {took}.")
    elif "human_detected" in states:
        sentence = f"A person answered; the call ended after {took}."
    else:
        sentence = f"No person reached; the call ended after {took}."
    said = []
    for line in call.get("transcript", []):
        who = {"hermes": "Hermes", "business": "They"}.get(line["speaker"])
        text = " ".join(str(line["text"]).split())
        if who and text:
            item = f'{who}: "{text[:160]}{"…" if len(text) > 160 else ""}"'
            if not said or said[-1] != item:
                said.append(item)
    on_call = ("On the call: " + " / ".join(said[-12:]) + ".") if said else ""
    return " ".join(p for p in (sentence, log, note, on_call) if p)[:1500]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--speed", type=float, default=1.0, help=">1 replays faster (clocks then run fast too)")
    ap.add_argument("--summary", choices=["round37", "recorded"], default="round37")
    args = ap.parse_args()

    db = Path(args.db).resolve()
    if not any("scratch" in p for p in db.parts):
        sys.exit(f"refusing to write outside a scratch directory: {db}")
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db}{suffix}").unlink(missing_ok=True)

    trial = json.loads(Path(args.evidence).read_text())
    call = trial["call"]
    events = trial.get("events", [])
    dialing = next((e["ts"] for e in events if e["kind"] == "dialing"), iso(call["started_at"]))
    origin = dialing

    # One timeline of (offset, action) from the recorded clocks (events and transcript share it).
    steps: list[tuple[float, str, object]] = [(iso(call["started_at"]) - origin, "started", None)]
    steps += [(iso(l["ts"]) - origin, "line", l) for l in call["transcript"]]
    timed_out = sorted(e["ts"] for e in events if e["kind"] == "owner_ring_timeout")
    for e in events:
        if e["kind"] != "state":
            continue
        to = e["data"]["to"]
        # The engine names the phase `fallback` when Hermes carries on after the owner's ring timed out.
        fallback = to == "agent_talking" and e["data"].get("frm") == "ringing_owner" and any(t <= e["ts"] for t in timed_out)
        steps.append((e["ts"] - origin, "state", (to, "fallback" if fallback else None)))
    end = next((e for e in events if e["kind"] == "ended"), None)
    steps.append(((end["ts"] if end else iso(call["state_since"])) - origin, "end", call))
    steps.sort(key=lambda s: s[0])

    store = Store(db)  # real clock: live durations in the pane run against real time, as on a live call
    task = store.create_task(title=call["task_title"], goal=call["task_title"], destination_e164=call["counterpart_masked"],
                             destination_masked=call["counterpart_masked"], mode=Mode(call["mode"]),
                             origin=Origin(profile="trial-replay"))
    rec = store.create_call(task_id=task.id, direction=Direction(call["direction"]), counterpart_e164=call["counterpart_masked"],
                            counterpart_masked=call["counterpart_masked"], state=CallState.DIALING, mode=Mode(call["mode"]))
    t0 = time.time()
    print(json.dumps({"replay": "started", "call": rec.id, "source_call": call["id"], "steps": len(steps)}), flush=True)
    for offset, kind, payload in steps:
        due = t0 + max(0.0, offset) / args.speed
        if due > time.time():
            time.sleep(due - time.time())
        if kind == "started":
            store.update_call(rec.id, started_at=time.time())
        elif kind == "line":
            store.append_transcript(rec.id, payload["speaker"], payload["text"])
        elif kind == "state":
            to, phase = payload
            store.update_call(rec.id, state=CallState(to), phase=phase)
        elif kind == "end":
            summary = payload.get("summary")
            if args.summary == "round37":
                summary = round37_summary(payload, events)
                print(json.dumps({"replay": "summary", "reconstructed": True, "text": summary}), flush=True)
            store.update_call(rec.id, state=CallState(payload["state"]), ended_at=time.time(),
                              end_reason=payload.get("end_reason") or "", summary=summary)
        print(json.dumps({"replay": kind, "at_offset": round(offset, 2)}), flush=True)
    store.close()
    print(json.dumps({"replay": "done"}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
