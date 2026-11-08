"""Round 9 live check (R1 delta 4 N4/N6): the full-scope guard on real ``hermes acp`` sessions.

Real ``hermes acp`` viewer on a scratch gateway in a scratch ``HERMES_HOME`` (never Can's gateway), the local fixture
model, the call service on loopback with a fake Twilio adapter and a fake ConversationRelay client. The scratch
home's tool configuration comes from ``L1_TOOL_CONFIG_YAML`` (for the restricted case: the tool-relevant keys of the
real ``hermes-line-calls`` profile, copied read-only; no credentials). Unlike the earlier fixture runs, the detector's
real ``ask`` session is used, so both the primary and the auxiliary session are checked live.

Expectation from ``L1_EXPECT``: ``pass`` (the call finishes through the session MCP, both sessions verified) or
``refuse`` (nothing is dialled). No phone, no Twilio, no real model, loopback only.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE.parent))

import fixture_model  # noqa: E402
from harness import Line, wait_for  # noqa: E402

HOME = Path(os.environ["HERMES_HOME"])
assert "hermes-line-l1" in str(HOME), "refusing to run outside the L1 scratch home"


def main(out_path: str) -> None:
    from plugin.line.engine.acp_driver import AcpDriver

    expect = os.environ.get("L1_EXPECT", "pass")
    model = fixture_model.start(0)
    (HOME / "config.yaml").write_text(
        f"model:\n  default: {fixture_model.MODEL}\n  provider: custom\n"
        f"  base_url: http://127.0.0.1:{model.server_address[1]}/v1\n  api_key: offline-fixture\n"
        + os.environ.get("L1_TOOL_CONFIG_YAML", ""))
    drivers = []

    def make_driver(sid):
        drv = AcpDriver(sid, command=json.loads(os.environ["L1_ACP_COMMAND"]), env=None, cwd=os.getcwd(),
                        start_timeout_s=90, turn_timeout_s=90)
        drivers.append(drv)
        return drv

    line = Line(driver_factory=make_driver, turn_timeout_s=90.0, ask_timeout_s=30.0, max_call_minutes=30)
    report = {"what": "real hermes acp, scratch gateway, fixture model; primary and detector sessions",
              "expect": expect, "tool_config": os.environ.get("L1_TOOL_CONFIG_LABEL", "")}
    ok = False
    try:
        req = line.request(title="Table for 4, Friday 7 pm", goal="Book a table for four on Friday at 7 pm.")
        out = line.approve(req["task_id"])["result"]
        report["approve_result"] = out
        report["provider_calls"] = len(line.adapter.created)
        drv = drivers[0] if drivers else None
        report["primary_tools_reply"] = (drv.session_tools_reply or "")[:600] if drv else None
        if expect == "refuse":
            wait_for(lambda: any(e["kind"] == "agent_start_failed" for e in line.store.events(req["call_id"])),
                     timeout=60, what="refusal")
            report["refusal"] = [e["data"] for e in line.store.events(req["call_id"])
                                 if e["kind"] in ("agent_start_failed", "dial_refused")]
            ok = not out["ok"] and report["provider_calls"] == 0
        else:
            created = line.adapter.created[-1]
            relay = line.connect(req, created)
            relay.prompt("Good evening, Sample Bistro, this is Mike speaking.")
            relay.wait_text("live person?", timeout=60)
            wait_for(lambda: drv.ask_tools_reply is not None or drv._ask_refused, timeout=90, what="ask session")
            relay.prompt("Yes, I am a real person")
            line.wait_state(req["call_id"], "agent_talking", timeout=60)
            relay.prompt("Alright, you're all set for Friday at seven thirty for four. FINISHNOW")
            try:
                wait_for(lambda: line.store.get_call(req["call_id"]).state.terminal, timeout=120, what="call end")
                wait_for(lambda: req["call_id"] not in line.svc.sessions, timeout=40, what="session finished")
            except AssertionError as exc:
                report["wait_error"] = str(exc)
                report["event_kinds"] = [e["kind"] for e in line.store.events(req["call_id"])][-30:]
                report["model_tool_results"] = [r[:300] for r in model.tool_results]
                report["model_tool_names"] = model.tool_names[-3:]
                report["state"] = line.store.get_call(req["call_id"]).state.value
                report["spoken"] = relay.spoken()[-300:]
            report.update({
                "ask_tools_reply": (drv.ask_tools_reply or "")[:600], "ask_refused": drv._ask_refused,
                "model_tool_calls": model.planned, "task_outcome": line.store.get_task(req["task_id"]).outcome,
                "end_reason": line.store.get_call(req["call_id"]).end_reason,
                "session_refusals": [e["data"] for e in line.store.events(req["call_id"])
                                     if e["kind"] == "agent_session_refused"],
                "tokens_active_after_end": line.svc.call_tools.active()})
            ok = (out["ok"] and report["task_outcome"] == "done" and not drv._ask_refused
                  and report["tokens_active_after_end"] == 0 and not report["session_refusals"])
    finally:
        line.close()
        model.shutdown()
    report["pass"] = bool(ok)
    text = json.dumps(report, indent=2)
    assert "Bearer" not in text
    Path(out_path).write_text(text)
    print(text)
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main(sys.argv[1])
