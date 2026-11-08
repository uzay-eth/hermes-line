#!/usr/bin/env python3
"""Seed a Hermes Line call store (line.db) from a preview fixture scene, through L1's own Store API.

Used by desktop-run.mjs to show the real pane inside a real Hermes Desktop against the plugin's
real REST API (plugin/line/server/desktop_api.py). No adapter, no telephony and no network: the
rows are written the way the engine writes them (create_task, create_call, append_transcript,
update_call), with the Store clock set to each fixture timestamp shifted by --offset seconds, so a
scene's `now` lands on the real launch time and the backend's live durations come out right.

    python seed_store.py --db <home>/plugin-data/hermes-line/line.db --scene human_detected --offset 1234.5

SAMPLE DATA only (fixtures/*.json). Refuses to write anywhere except a fresh file under a path
containing "scratch".
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from plugin.line.engine.contracts import CallState, Direction, Mode, Origin  # noqa: E402
from plugin.line.store.db import Store  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SAMPLE_E164 = "+15550142"  # fictional 555-01xx sample range


def ts(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--scene", required=True)
    ap.add_argument("--offset", type=float, required=True, help="seconds added to every fixture time")
    args = ap.parse_args()

    db = Path(args.db).resolve()
    if "scratch" not in db.parts and not any("scratch" in p for p in db.parts):
        sys.exit(f"refusing to seed outside a scratch directory: {db}")
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db}{suffix}").unlink(missing_ok=True)

    snapshot = json.loads((FIXTURES / f"{args.scene}.json").read_text())
    clock = [0.0]
    store = Store(db, clock=lambda: clock[0])
    # Oldest first, so creation order (and the backend's list order) follows the day.
    for call in sorted(snapshot["calls"], key=lambda c: ts(c["started_at"])):
        at = lambda iso: ts(iso) + args.offset  # noqa: E731
        start = at(call["started_at"])
        clock[0] = start
        task = store.create_task(title=call["task_title"], goal=call["task_title"], destination_e164=SAMPLE_E164,
                                 destination_masked=call["counterpart_masked"], mode=Mode(call["mode"]),
                                 origin=Origin(profile="preview-sample"))
        rec = store.create_call(task_id=task.id, direction=Direction(call["direction"]), counterpart_e164=SAMPLE_E164,
                                counterpart_masked=call["counterpart_masked"], state=CallState.DIALING,
                                mode=Mode(call["mode"]))
        if call["state"] != "awaiting_approval":
            store.update_call(rec.id, started_at=start)
        for line in call["transcript"]:
            clock[0] = at(line["ts"])
            store.append_transcript(rec.id, line["speaker"], line["text"])
        clock[0] = at(call["state_since"])
        fields = {"state": CallState(call["state"])}
        if call["state"] in ("ended", "failed"):
            fields["ended_at"] = start + call["duration_s"]
            if call["summary"]:
                fields["summary"] = call["summary"]
        store.update_call(rec.id, **fields)
    store.close()
    print(f"seeded {len(snapshot['calls'])} call(s) for {args.scene} into {db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
