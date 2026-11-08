"""Round 6 live check: a real ``hermes acp`` session calls ``hermes_line_call_finish`` through the session MCP.

The call service runs on loopback (fake Twilio adapter, fake ConversationRelay client). Its Hermes
session is a real ``hermes acp`` viewer on a scratch gateway in a scratch ``HERMES_HOME`` (the viewer
starts it, as in round 2; never Can's gateway). The plugin is NOT installed in that home, so the only
way the model can reach the in-call tools is the session MCP server the driver passes in ``session/new``.
The model is the local fixture (``fixture_model.py``): it calls the finish tool when the business line
contains FINISHNOW. No phone, no Twilio, no real model, no network beyond loopback.

Run inside the L1 offline sandbox (see docs/lanes/L1.md section 14):
    HERMES_HOME=<scratch home> L1_ACP_COMMAND='[...]' python -B session_mcp_fixture_run.py OUT.json
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))  # tests/l1 (harness)
sys.path.insert(0, str(HERE.parent))

import fixture_model  # noqa: E402
from harness import Line, wait_for  # noqa: E402

HOME = Path(os.environ["HERMES_HOME"])
assert "hermes-line-l1" in str(HOME), "refusing to run outside the L1 scratch home"


def main(out_path: str) -> None:
    from plugin.line.engine.acp_driver import AcpDriver

    model = fixture_model.start(0)
    port = model.server_address[1]
    (HOME / "config.yaml").write_text(
        f"model:\n  default: {fixture_model.MODEL}\n  provider: custom\n  base_url: http://127.0.0.1:{port}/v1\n"
        "  api_key: offline-fixture\n" + os.environ.get("L1_EXTRA_CONFIG_YAML", ""))  # round 8 toolset probe
    plugin_installed = (HOME / "plugins" / "hermes-line").exists()
    drivers = []

    class Driver(AcpDriver):
        def ask(self, prompt, timeout_s=20.0):
            return ""  # no second ACP session for detector/summary questions

    def make_driver(sid):
        drv = Driver(sid, command=json.loads(os.environ["L1_ACP_COMMAND"]), env=None, cwd=os.getcwd(),
                     start_timeout_s=90, turn_timeout_s=90)
        drivers.append(drv)
        return drv

    line = Line(driver_factory=make_driver, turn_timeout_s=90.0, ask_timeout_s=30.0, max_call_minutes=30)
    report = {"what": "real hermes acp (scratch gateway, fixture model) -> session MCP -> call service",
              "plugin_installed_in_gateway_home": plugin_installed}
    if os.environ.get("L1_EXPECT_REFUSAL"):  # round 8: an unrestricted profile must dial nobody
        try:
            req = line.request(title="Table for 4, Friday 7 pm", goal="Book a table for four on Friday at 7 pm.")
            out = line.approve(req["task_id"])["result"]
            wait_for(lambda: any(e["kind"] == "agent_start_failed" for e in line.store.events(req["call_id"])),
                     timeout=120, what="refusal recorded")
            report.update({"approve_result": out, "provider_calls": len(line.adapter.created),
                           "final_state": line.store.get_call(req["call_id"]).state.value,
                           "refusal": [e["data"] for e in line.store.events(req["call_id"])
                                       if e["kind"] in ("agent_start_failed", "dial_refused")],
                           "session_tools_reply": (drivers[0].session_tools_reply or "")[:600] if drivers else None})
        finally:
            line.close()
            model.shutdown()
        text = json.dumps(report, indent=2)
        Path(out_path).write_text(text)
        print(text)
        ok = not report["approve_result"]["ok"] and report["provider_calls"] == 0
        print("PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
    try:
        req, created = line.dial(title="Table for 4, Friday 7 pm",
                                 goal="Book a table for four on Friday at 7 pm under the name Sam.")
        relay = line.connect(req, created)
        relay.prompt("Good evening, Sample Bistro, this is Mike speaking.")
        relay.wait_text("live person?", timeout=20)
        wait_for(lambda: drivers and drivers[0].hermes_session_id, timeout=120, what="ACP session")
        relay.prompt("Yes, I am a real person")
        line.wait_state(req["call_id"], "agent_talking", timeout=60)
        relay.prompt("Alright, you're all set for Friday at seven thirty for four. FINISHNOW")
        try:
            wait_for(lambda: line.store.get_call(req["call_id"]).state.terminal, timeout=120, what="call end")
            wait_for(lambda: req["call_id"] not in line.svc.sessions, timeout=30, what="session finished")
        except AssertionError as exc:
            report["wait_error"] = str(exc)
            report["event_kinds"] = [e["kind"] for e in line.store.events(req["call_id"])][-40:]
            report["turn_events"] = [e["data"] for e in line.store.events(req["call_id"], kind="turn")]
            report["model_requests"] = len(model.requests)
            report["tool_names_per_request"] = model.tool_names
            report["last_user_messages"] = [fixture_model._text(([m for m in r.get("messages", [])
                                                                  if m.get("role") == "user"] or [{}])[-1].get("content"))[-160:]
                                            for r in model.requests]
            report["rejected"] = [e["data"] for e in line.store.events() if e["kind"] == "call_tools_rejected"]
        call = line.store.get_call(req["call_id"])
        task = line.store.get_task(req["task_id"])
        events = line.store.events(req["call_id"])
        offered = [n for names in model.tool_names for n in names]
        report.update({
            "acp_session_id": drivers[0].hermes_session_id if drivers else None,
            "mcp_servers_in_session_new": [{"name": s["name"], "type": s["type"],
                                            "url_host": s["url"].split("/")[2].split(":")[0],
                                            "header_names": [h["name"] for h in s["headers"]]}
                                           for s in (drivers[0]._mcp_servers if drivers else [])],
            "tool_calls_by_model": model.planned,
            "finish_tools_reached": sorted({n for n in model.planned if n.endswith("hermes_line_call_finish")}),
            "in_process_tools_offered": sorted({n for n in offered if n.startswith("hermes_line_")}),
            "all_tools_offered_first_request": model.tool_names[0] if model.tool_names else [],
            "tool_results_seen_by_model": model.tool_results,
            "finish_requested_event": [e["data"] for e in events if e["kind"] == "finish_requested"],
            "final_state": call.state.value, "end_reason": call.end_reason, "task_outcome": task.outcome,
            "tokens_active_after_end": line.svc.call_tools.active(),
            "session_tools_reply": (drivers[0].session_tools_reply or "")[:600] if drivers else None,
            "spoken": relay.spoken()[-300:],
        })
    finally:
        line.close()
        model.shutdown()
    text = json.dumps(report, indent=2)
    assert "Bearer" not in text
    Path(out_path).write_text(text)
    print(text)
    ok = (report.get("finish_requested_event") and report.get("task_outcome") == "done"
          and report.get("finish_tools_reached") and not report.get("in_process_tools_offered")
          and not plugin_installed and report.get("tokens_active_after_end") == 0)
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main(sys.argv[1])
