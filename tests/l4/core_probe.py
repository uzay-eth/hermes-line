"""Run line.sms against the REAL Hermes core, read-only, in a throwaway HERMES_HOME.

Not a unit test: `test_core_integration.py` launches this file with Hermes's own interpreter and
reads the JSON it prints. Usage:  HERMES_AGENT_DIR=... <hermes python> core_probe.py <plugin_dir>

What it exercises (all real Hermes code): PluginManager/PluginContext registration, the registry,
`model_tools.handle_function_call` with its pre/post/transform hook dispatch, `ctx.dispatch_tool`,
and `hermes_state.SessionDB` storage. Nothing is written outside the temp HERMES_HOME; Hermes source
is not modified. No network, no phone.
"""
import json
import os
import sqlite3
import sys
import tempfile

HERMES_DIR = os.environ["HERMES_AGENT_DIR"]
PLUGIN_DIR = sys.argv[1]
HOME = tempfile.mkdtemp(prefix="l4-core-")
os.environ["HERMES_HOME"] = HOME
os.environ.pop("HERMES_REDACT_SECRETS", None)
sys.path[:0] = [PLUGIN_DIR, HERMES_DIR]

import logging  # noqa: E402

logging.disable(logging.CRITICAL)

import hermes_cli.plugins as plugins_mod  # noqa: E402
import model_tools  # noqa: E402
from hermes_cli.plugins import PluginContext, PluginManager  # noqa: E402
from hermes_cli.plugins_manifest import PluginManifest  # noqa: E402
from tools.registry import registry  # noqa: E402

from line.sms import ApprovedTask, FixtureSource, SmsMessage, scrub  # noqa: E402
from line.policy.consent import ConsentStore  # noqa: E402
from line.sms import Destination, MemoryReleasedStore  # noqa: E402
from line.sms.consent import OTP_ACTION  # noqa: E402
from line.sms.delivery import BrowserOriginProbe, BrowserTypeSink, CallSink, OtpDelivery  # noqa: E402
from line.sms.hooks import CALL_TOOL, FILL_TOOL, register  # noqa: E402
from line.sms.service import OtpService, OtpTaskRegistry, binding_for  # noqa: E402

CODE = "739204"
T0 = 1793836800000  # 2026-11-05T00:00:00Z
NOW = (T0 + 120_000) / 1000.0


def run(kind):
    scrub.DEFAULT.clear()
    mgr = PluginManager()
    plugins_mod._plugin_manager = mgr
    ctx = PluginContext(PluginManifest(name="hermes-line-l4-probe-%s" % kind, source="user"), mgr)

    seen_tools = []
    ctx.register_hook("post_tool_call", lambda **kw: seen_tools.append(kw.get("tool_name")))

    pages = {}
    sent = []

    def reg(name, handler):
        registry.register(
            name=name, toolset="l4probe-browser",
            schema={"name": name, "description": "probe", "parameters": {"type": "object", "properties": {}}},
            handler=handler, override=True,
        )

    reg("browser_type", lambda args, **kw: (pages.__setitem__(args["ref"], args["text"]) or
                                            json.dumps({"success": True, "typed": args["text"], "element": args["ref"]})))
    reg("browser_console", lambda args, **kw: json.dumps({"success": True, "result": "https://example.com", "result_type": "str"}))
    reg("l4_snapshot", lambda args, **kw: json.dumps({"page": "textbox value %s" % CODE}))

    consent = ConsentStore(clock=lambda: NOW)
    msg = SmsMessage("1", "ACME", T0 + 60_000, "Your ACME code is %s." % CODE, source="fixture")
    svc = OtpService(FixtureSource([msg], clock=lambda: NOW), OtpTaskRegistry(), released=MemoryReleasedStore(),
                     clock=lambda: NOW, sleep=lambda s: None)
    dest = Destination.fill("https://example.com") if kind == "fill" else Destination.call("call-1", "+15550100999")
    task = ApprovedTask("t1", ("ACME",), T0, T0 + 600_000, destination=dest)
    svc.registry.register(task)
    token = consent.issue(OTP_ACTION, "t1", binding_for(task), 600)
    delivery = OtpDelivery(svc, BrowserTypeSink(ctx.dispatch_tool), origin_probe=BrowserOriginProbe(ctx.dispatch_tool),
                           call_sink=CallSink(lambda cid, secret, mode, approval_generation=None: sent.append((cid, mode, secret.reveal())) or True),
                           otp_browser_fill=True, clock=lambda: NOW)
    register(ctx, delivery, consent, lambda tid: token)

    ids = dict(task_id="browser-1", session_id="s1", turn_id="turn-1", api_request_id="req-1")
    out = {"kind": kind, "tools_registered": sorted(n for n in (FILL_TOOL, CALL_TOOL, "line_otp_read") if registry.get_entry(n) is not None)}

    first_tool, args = (FILL_TOOL, {"task_id": "t1", "targets": ["@e7"]}) if kind == "fill" else (
        CALL_TOOL, {"task_id": "t1", "call_id": "call-1", "mode": "dtmf"})
    r1 = model_tools.handle_function_call(first_tool, args, tool_call_id="tc1", skip_pre_tool_call_hook=True, **ids)
    r2 = model_tools.handle_function_call("l4_snapshot", {}, tool_call_id="tc2", skip_pre_tool_call_hook=True, **ids)
    out.update(first_result=r1, snapshot_result=r2, pages=pages, sent=sent, post_tool_call_tools=seen_tools)

    # What Hermes core does with a tool result next: store it in state.db. Use the real SessionDB.
    from hermes_state import SessionDB

    db_path = os.path.join(HOME, "state-%s.db" % kind)
    db = SessionDB(db_path=__import__("pathlib").Path(db_path))
    db.create_session(session_id="s-%s" % kind, source="cli", model="m")
    for i, content in enumerate((r1, r2)):
        db.append_message(session_id="s-%s" % kind, role="tool", content=content,
                          tool_name=first_tool if i == 0 else "l4_snapshot", tool_call_id="tc%d" % (i + 1))
    db.close()
    con = sqlite3.connect(db_path)
    rows = [r[0] for r in con.execute("select content from messages order by id")]
    con.close()
    out["db_rows_with_code"] = [CODE in (row or "") for row in rows]
    return out


print(json.dumps({"fill": run("fill"), "call": run("call")}))
