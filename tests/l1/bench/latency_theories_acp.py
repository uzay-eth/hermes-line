"""Round 12: latency theories on the real ACP stack (live calls 2 and 3).

Real ``hermes acp`` on a scratch gateway in a scratch ``HERMES_HOME`` (call profile: ``platform_toolsets: {acp: []}``),
the fixture model slowed to model-like timing (first token ``L1_FIRST_DELAY`` s, then ``L1_CHUNK_DELAY`` s per word),
the call service on loopback, fake ConversationRelay. Measured from the moment a business line is sent to the first
spoken Hermes text that is not a fixed filler:

* ``normal``: a turn with nothing in flight (outbound and inbound);
* ``barge_in``: the live pattern, a second fragment 1.3 s after the first while Hermes is still thinking;
* ``finish``: the finish turn (does the model need ``tool_search`` first?).

No phone, no Twilio, no real model, loopback only.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE.parent))

import fixture_model  # noqa: E402
from harness import BUSINESS, LINE, OWNER, Line, wait_for  # noqa: E402
from plugin.line.engine.machine import FILLER_LINES  # noqa: E402

HOME = Path(os.environ["HERMES_HOME"])
assert "hermes-line-l1" in str(HOME), "refusing to run outside the L1 scratch home"


def spoken_latency(relay, text, timeout=30.0):
    before = len(relay.frames)
    t0 = time.monotonic()
    relay.prompt(text)
    while time.monotonic() - t0 < timeout:
        for f in relay.frames[before:]:
            tok = (f.get("token") or "").strip()
            if f.get("type") == "text" and tok and tok not in FILLER_LINES:
                return round(time.monotonic() - t0, 2), before
        time.sleep(0.01)
    return None, before


def settle(relay, line, call_id, seconds=6.0):
    """Wait until Hermes's reply is done (no turn running)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        s = line.session(call_id)
        if s is None or s.turn_task is None or s.turn_task.done():
            time.sleep(0.3)
            return
        time.sleep(0.05)


def run(direction):
    from plugin.line.engine.acp_driver import AcpDriver
    model = fixture_model.start(0)
    (HOME / "config.yaml").write_text(
        f"model:\n  default: {fixture_model.MODEL}\n  provider: custom\n"
        f"  base_url: http://127.0.0.1:{model.server_address[1]}/v1\n  api_key: offline-fixture\n"
        "platform_toolsets:\n  acp: []\n")
    drivers = []

    def make_driver(sid):
        d = AcpDriver(sid, command=json.loads(os.environ["L1_ACP_COMMAND"]), env=None, cwd=os.getcwd(),
                      start_timeout_s=90, turn_timeout_s=90)
        drivers.append(d)
        return d

    line = Line(driver_factory=make_driver, owner_number=BUSINESS, owner_name="Can",
                policy={"allowed_destinations": [BUSINESS, OWNER]}, turn_timeout_s=60.0, ask_timeout_s=20.0,
                max_call_minutes=30)
    out = {"direction": direction}
    try:
        if direction == "outbound":
            req, created = line.dial(title="Line test")
            call_id = req["call_id"]
            relay = line.connect(req, created)
        else:
            sid = "CA" + "7" * 32
            status, body = line.post("/twilio/voice/inbound", {"CallSid": sid, "From": "+17755550177", "To": LINE})
            call_id = [c for c in line.store.list_calls() if c.direction.value == "inbound"][0].id
            wait_for(lambda: line.session(call_id) and line.session(call_id).driver is not None, timeout=90,
                     what="inbound agent warm")
            relay = line.relay(body)
            relay.send({"type": "setup", "callSid": sid})
            time.sleep(0.5)
        lat = {"normal": [], "barge_in": [], "finish": None}
        for text in ("Hi. How are you?", "Can you hear me clearly?"):
            got, _ = spoken_latency(relay, text)
            lat["normal"].append(got)
            settle(relay, line, call_id)
        # the live pattern: a second fragment 1.3 s after the first, Hermes still thinking
        relay.prompt("I am Ken.")
        time.sleep(1.3)
        got, _ = spoken_latency(relay, "Do you know about me?")
        lat["barge_in"].append(got)
        settle(relay, line, call_id, 12.0)
        got, _ = spoken_latency(relay, "No. That's good, thank you. FINISHNOW", timeout=40)
        lat["finish"] = got
        wait_for(lambda: line.store.get_call(call_id).state.terminal, timeout=60, what="end")
        out["latency_s"] = lat
        out["model_tool_calls"] = model.planned
        out["turn_events"] = [e["data"] for e in line.store.events(call_id, kind="turn")]
        out["barge_ins"] = sum(1 for e in line.store.events(call_id) if e["kind"] == "barge_in")
        out["first_prompt_chars"] = len(json.dumps(model.requests[1]["messages"])) if len(model.requests) > 1 else None
    finally:
        line.close()
        model.shutdown()
    return out


def main(out_path):
    fixture_model.FIRST_DELAY = float(os.environ.get("L1_FIRST_DELAY", "1.2"))
    fixture_model.CHUNK_DELAY = float(os.environ.get("L1_CHUNK_DELAY", "0.25"))
    report = {"what": "latency theories, real hermes acp + scratch gateway + slowed fixture model",
              "model_first_delay_s": fixture_model.FIRST_DELAY, "model_chunk_delay_s": fixture_model.CHUNK_DELAY,
              "label": os.environ.get("L1_LABEL", ""), "runs": [run("outbound"), run("inbound")]}
    text = json.dumps(report, indent=2)
    Path(out_path).write_text(text)
    print(text)


if __name__ == "__main__":
    main(sys.argv[1])
