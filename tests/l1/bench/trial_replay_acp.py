"""Round 7: replay the first live call's transcript through the real ACP driver and time every reply.

Real ``hermes acp`` viewer on a scratch gateway in a scratch ``HERMES_HOME`` (never Can's), the local
fixture model (whose answers to the detector are not JSON, so the detector says unknown/low as it did
live), the call service on loopback with the shipped confirm cooldown (45 s), a fake ConversationRelay
client. Cases: a business number, and the owner's own number (self-test), each with a fast model and
with a model that needs 4 s before its first token. Every human line must get spoken text back within
3 s. No phone, no Twilio, no real model, loopback only.
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
from harness import BUSINESS, OWNER, Line, wait_for  # noqa: E402

HOME = Path(os.environ["HERMES_HOME"])
assert "hermes-line-l1" in str(HOME), "refusing to run outside the L1 scratch home"
TRIAL = ["Hi. How are you?", "Yeah. It's me, Ken. But information you have about me?"]
BUDGET_S = 3.0


def latency(relay, text, timeout=20.0):
    before = len(relay.frames)
    t0 = time.monotonic()
    relay.prompt(text)
    while time.monotonic() - t0 < timeout:
        if any(f.get("type") == "text" and (f.get("token") or "").strip() for f in relay.frames[before:]):
            return round(time.monotonic() - t0, 3), before
        time.sleep(0.005)
    return None, before


def run_case(name, *, owner_self_test, first_delay):
    from plugin.line.engine.acp_driver import AcpDriver

    fixture_model.FIRST_DELAY = first_delay
    model = fixture_model.start(0)
    (HOME / "config.yaml").write_text(
        f"model:\n  default: {fixture_model.MODEL}\n  provider: custom\n"
        f"  base_url: http://127.0.0.1:{model.server_address[1]}/v1\n  api_key: offline-fixture\n"
        "platform_toolsets:\n  acp: []\n")  # round 8: a call profile (call tools only), as the guard requires
    drivers = []

    def make_driver(sid):
        drv = AcpDriver(sid, command=json.loads(os.environ["L1_ACP_COMMAND"]), env=None, cwd=os.getcwd(),
                        start_timeout_s=90, turn_timeout_s=60)
        drivers.append(drv)
        return drv

    kw = {"owner_number": BUSINESS, "owner_name": "Can",
          "policy": {"allowed_destinations": [BUSINESS, OWNER]}} if owner_self_test else {"owner_name": "Can"}
    line = Line(driver_factory=make_driver, confirm_cooldown_s=45.0, turn_timeout_s=60.0, ask_timeout_s=20.0,
                max_call_minutes=30, **kw)
    out = {"case": name, "owner_self_test": owner_self_test, "model_first_token_delay_s": first_delay}
    try:
        req, created = line.dial(title="Attended owner trial")
        line.session(req["call_id"]).warm()
        wait_for(lambda: drivers and drivers[0].hermes_session_id, timeout=120, what="ACP session")
        relay = line.connect(req, created)
        turns = []
        for text in TRIAL:
            lat, before = latency(relay, text)
            time.sleep(max(0.0, (first_delay + 1.5)))  # let the reply finish before the next line, as live
            spoken = "".join(f.get("token", "") for f in relay.frames[before:] if f.get("type") == "text")
            turns.append({"business": text, "reply_latency_s": lat, "hermes_said": spoken.strip()[:200]})
        out["turns"] = turns
        out["events"] = [e["kind"] for e in line.store.events(req["call_id"])
                         if e["kind"] in ("detect", "detect_slow", "filler", "owner_self_test", "human_assumed",
                                          "state")]
        out["detect"] = [e["data"] for e in line.store.events(req["call_id"]) if e["kind"] == "detect"]
        out["final_state_before_hangup"] = line.store.get_call(req["call_id"]).state.value
        t0 = time.monotonic()
        status = line.status(req["call_id"], created["sid"], "completed")
        out["status_callback_s"] = round(time.monotonic() - t0, 3)
        out["status_callback_http"] = status
        wait_for(lambda: req["call_id"] not in line.svc.sessions, timeout=40, what="session finished")
        out["summary"] = (line.store.get_call(req["call_id"]).summary or "")[:160]
    finally:
        line.close()
        model.shutdown()
    out["pass"] = (all(t["reply_latency_s"] is not None and t["reply_latency_s"] <= BUDGET_S for t in out["turns"])
                   and out["status_callback_s"] < 1.0)
    return out


def main(out_path: str) -> None:
    cases = [run_case("business, fast model", owner_self_test=False, first_delay=0.02),
             run_case("business, model 4 s to first token", owner_self_test=False, first_delay=4.0),
             run_case("owner self-test, fast model", owner_self_test=True, first_delay=0.02),
             run_case("owner self-test, model 4 s to first token", owner_self_test=True, first_delay=4.0)]
    report = {"what": "first live call transcript replayed through real hermes acp (scratch gateway, fixture model)",
              "budget_s": BUDGET_S, "cases": cases}
    text = json.dumps(report, indent=2)
    Path(out_path).write_text(text)
    print(text)
    ok = all(c["pass"] for c in cases)
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main(sys.argv[1])
