"""Tool + hook wiring, and a model of what Hermes core persists.

`CoreModel` reproduces the three facts the L4 design rests on (each verified in the Hermes source,
docs/lanes/L4.md section 1): the tool handler's string goes through `transform_tool_result` and then
straight into the session store; the final reply goes through `transform_llm_output` and then into
the store; `ctx.dispatch_tool` runs the handler only (no hooks, no store). The store is a list of
strings, so "the code does not persist" is `assertNotIn(code, "\\n".join(store))`.
"""
import json
import logging
import unittest

from line.sms import ApprovedTask, FixtureSource, MemoryReleasedStore, SmsMessage
from line.sms import scrub
from line.policy.consent import ConsentStore
from line.sms.consent import OTP_ACTION
from line.sms.delivery import BrowserOriginProbe, BrowserTypeSink, CallSink, OtpDelivery
from line.sms.hooks import (
    FILL_SCHEMA,
    FILL_TOOL,
    CALL_SCHEMA,
    CALL_TOOL,
    make_llm_output_hook,
    make_tool_result_hook,
    register,
)
from line.sms.service import OtpService, OtpTaskRegistry, binding_for

from .helpers import CALL_DEST, FILL_DEST, MIN, T0

CODE = "739204"
BODY = "Your ACME code is %s. Do not share it." % CODE


class FakeCtx:
    """The slice of Hermes's PluginContext that line.sms uses."""

    def __init__(self):
        self.tools = {}
        self.hooks = {}
        self.dispatched = []
        self.pages = {}          # ref -> typed text (the "browser")
        self.page_origin = "https://example.com"

    def register_tool(self, name, toolset, schema, handler, check_fn=None, requires_env=None, is_async=False,
                      description="", emoji="", override=False):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler, "check_fn": check_fn}

    def register_hook(self, name, cb):
        self.hooks.setdefault(name, []).append(cb)

    def dispatch_tool(self, name, args, **kw):
        self.dispatched.append((name, dict(args), dict(kw)))
        if name == "browser_console":
            return json.dumps({"success": True, "result": self.page_origin, "result_type": "str"})
        assert name == "browser_type"
        self.pages[args["ref"]] = args["text"]
        return json.dumps({"success": True, "typed": args["text"], "element": args["ref"]})


class CoreModel:
    """Order of events in Hermes core for one tool call (model_tools.py:956-960, tool_executor.py:1155-1173)."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.store = []        # session DB rows (content strings)
        self.events = []       # what UI/gateway event channels would carry (tool.completed result)

    def call_tool(self, name, args, **kw):
        result = self.ctx.tools[name]["handler"](args, **kw)
        for cb in self.ctx.hooks.get("transform_tool_result", []):
            out = cb(tool_name=name, args=args, result=result)
            if isinstance(out, str):
                result = out
                break
        self.store.append(result)      # canonical append + flush, before the model sees it
        self.events.append(result)     # tool.completed carries the same (transformed) string
        return result

    def tool_result_from_other_tool(self, name, text):
        """Another tool (page snapshot, terminal) whose output happens to contain the code."""
        result = text
        for cb in self.ctx.hooks.get("transform_tool_result", []):
            out = cb(tool_name=name, args={}, result=result)
            if isinstance(out, str):
                result = out
                break
        self.store.append(result)
        self.events.append(result)
        return result

    def final_reply(self, text):
        for cb in self.ctx.hooks.get("transform_llm_output", []):
            out = cb(response_text=text, session_id="s", model="m", platform="cli", turn_id="t")
            if isinstance(out, str) and out:
                text = out
                break
        self.store.append(text)
        return text

    def blob(self):
        return "\n".join(self.store + self.events)


class Base(unittest.TestCase):
    def setUp(self):
        scrub.DEFAULT.clear()
        self.addCleanup(scrub.DEFAULT.clear)
        self.t = (T0 + 2 * MIN) / 1000.0
        self.consent = ConsentStore(clock=lambda: self.t)
        self.source = FixtureSource([SmsMessage("1", "ACME", T0 + MIN, BODY, source="fixture")], clock=lambda: self.t)
        self.service = OtpService(self.source, OtpTaskRegistry(), released=MemoryReleasedStore(), clock=lambda: self.t, sleep=lambda s: None)
        task = ApprovedTask("t1", ("ACME",), T0, T0 + 10 * MIN, destination=FILL_DEST)
        self.service.registry.register(task)
        self.tokens = {"t1": self.consent.issue(OTP_ACTION, "t1", binding_for(task), 600)}
        self.ctx = FakeCtx()
        self.core = CoreModel(self.ctx)
        self.logs = []

        class H(logging.Handler):
            def emit(h, record):
                self.logs.append(record.getMessage())

        self.h = H(logging.DEBUG)
        lg = logging.getLogger("line.sms")
        lg.addHandler(self.h)
        old = lg.level
        lg.setLevel(logging.DEBUG)
        self.addCleanup(lg.removeHandler, self.h)
        self.addCleanup(lg.setLevel, old)

    def wire(self):
        self.sent = []
        delivery = OtpDelivery(
            self.service, BrowserTypeSink(self.ctx.dispatch_tool),
            origin_probe=BrowserOriginProbe(self.ctx.dispatch_tool),
            call_sink=CallSink(lambda call_id, secret, mode, approval_generation=None: self.sent.append((call_id, secret.reveal(), mode)) or True),
            otp_browser_fill=True, clock=lambda: self.t,
        )
        register(self.ctx, delivery, self.consent, lambda task_id: self.tokens.get(task_id))
        return delivery


class Registration(Base):
    def test_registers_the_two_sealed_tools_and_two_hooks(self):
        self.wire()
        self.assertEqual({FILL_TOOL, CALL_TOOL}, set(self.ctx.tools))
        self.assertEqual({"transform_tool_result", "transform_llm_output"}, set(self.ctx.hooks))
        self.assertEqual("hermes-line", self.ctx.tools[FILL_TOOL]["toolset"])

    def test_no_cleartext_tool_exists_anywhere(self):
        self.wire()
        self.assertFalse([n for n in self.ctx.tools if "read" in n])
        import line.sms.hooks as hooks_mod
        import line.sms.delivery as delivery_mod

        self.assertFalse(hasattr(hooks_mod, "READ_TOOL") or hasattr(delivery_mod, "DeliveryMode"))
        self.assertFalse(hasattr(OtpDelivery, "cleartext"))

    def test_schemas_offer_no_way_to_pass_a_token_or_a_sender(self):
        for schema in (FILL_SCHEMA, CALL_SCHEMA):
            props = set(schema["parameters"]["properties"])
            self.assertFalse(props & {"token", "consent", "consent_token", "sender", "senders", "window", "code", "origin", "counterpart"}, props)
            self.assertNotIn("additionalProperties", schema["parameters"], "extra args are ignored by the handler anyway")
        self.assertEqual(["dtmf", "speak"], CALL_SCHEMA["parameters"]["properties"]["mode"]["enum"])


class SealedEndToEnd(Base):
    def test_model_never_sees_the_code_and_core_never_stores_it(self):
        self.wire()
        result = self.core.call_tool(FILL_TOOL, {"task_id": "t1", "targets": ["@e7"]}, task_id="browser-1")
        body = json.loads(result)
        self.assertEqual("filled", body["status"])
        self.assertEqual({"status", "task_id", "filled_fields", "from"}, set(body))
        self.assertEqual("A***ME", body["from"])
        # the page got the code, through registry-level dispatch (no hooks fired by it)
        self.assertEqual({"@e7": CODE}, self.ctx.pages)
        self.assertEqual("browser-1", self.ctx.dispatched[0][2]["task_id"])
        # a later page snapshot and a model reply that quote the page both get scrubbed on the way in
        self.core.tool_result_from_other_tool("browser_snapshot", 'textbox [ref=@e7] value "%s"' % CODE)
        self.core.tool_result_from_other_tool("terminal", "echo %s\n%s" % (CODE, "7 3 9 2 0 4"))
        self.core.final_reply("I typed the code %s for you." % CODE)
        self.assertNotIn(CODE, self.core.blob())
        self.assertNotIn("7 3 9 2 0 4", self.core.blob())
        self.assertIn("[CODE]", self.core.blob())
        self.assertNotIn(CODE, "\n".join(self.logs))

    def test_unrelated_results_are_returned_untouched(self):
        self.wire()
        self.core.call_tool(FILL_TOOL, {"task_id": "t1", "targets": ["@e7"]})
        text = "plain page text with\u00a0nbsp and 12345 but no code"
        self.assertEqual(text, self.core.tool_result_from_other_tool("browser_snapshot", text))

    def test_handler_rejects_bad_arguments_without_a_traceback(self):
        self.wire()
        h = self.ctx.tools[FILL_TOOL]["handler"]
        for args in ({}, {"task_id": 5, "targets": ["@a"]}, {"task_id": "t1"}, {"task_id": "t1", "targets": 7}):
            out = json.loads(h(args))
            self.assertEqual("bad_request", out["status"], args)
        self.assertEqual({}, self.ctx.pages)

    def test_no_consent_lookup_means_no_release(self):
        self.tokens.clear()
        self.wire()
        out = json.loads(self.ctx.tools[FILL_TOOL]["handler"]({"task_id": "t1", "targets": ["@a"]}))
        self.assertEqual("no_consent", out["status"])
        self.assertEqual({}, self.ctx.pages)

    def test_extra_model_supplied_token_is_ignored(self):
        self.tokens.clear()
        self.wire()
        forged = self.consent.issue(OTP_ACTION, "t1", binding_for(self.service.registry.get("t1")), 600)
        out = json.loads(self.ctx.tools[FILL_TOOL]["handler"]({"task_id": "t1", "targets": ["@a"], "token": forged}))
        self.assertEqual("no_consent", out["status"], "a token in the arguments must not count")

    def test_handler_exception_is_reported_without_its_message(self):
        delivery = self.wire()

        def boom(*a, **k):
            raise RuntimeError("sink exploded typing %s" % CODE)

        delivery.fill = boom
        out = self.ctx.tools[FILL_TOOL]["handler"]({"task_id": "t1", "targets": ["@a"]})
        self.assertNotIn(CODE, out)
        self.assertNotIn(CODE, "\n".join(self.logs))
        self.assertEqual("error", json.loads(out)["status"])


class CallEndToEnd(Base):
    def setUp(self):
        super().setUp()
        self.ctask = ApprovedTask("tc", ("ACME",), T0, T0 + 10 * MIN, destination=CALL_DEST)
        self.service.registry.register(self.ctask)
        self.tokens["tc"] = self.consent.issue(OTP_ACTION, "tc", binding_for(self.ctask), 600)

    def test_code_goes_to_the_call_and_nowhere_else(self):
        self.wire()
        result = self.core.call_tool(CALL_TOOL, {"task_id": "tc", "call_id": "call-1", "mode": "dtmf"})
        self.assertEqual("sent", json.loads(result)["status"])
        self.assertEqual([("call-1", CODE, "dtmf")], self.sent)
        self.core.final_reply("I gave them the code %s." % CODE)
        self.core.tool_result_from_other_tool("terminal", "echo %s" % CODE)
        self.assertNotIn(CODE, self.core.blob())
        self.assertNotIn(CODE, "\n".join(self.logs))

    def test_handler_validates_arguments(self):
        self.wire()
        h = self.ctx.tools[CALL_TOOL]["handler"]
        for args in ({}, {"task_id": "tc"}, {"task_id": "tc", "call_id": "call-1", "mode": "email"}, {"task_id": 4, "call_id": "c", "mode": "dtmf"}):
            self.assertEqual("bad_request", json.loads(h(args))["status"], args)
        self.assertEqual([], self.sent)

    def test_wrong_call_is_refused(self):
        self.wire()
        out = json.loads(self.ctx.tools[CALL_TOOL]["handler"]({"task_id": "tc", "call_id": "call-77", "mode": "speak"}))
        self.assertEqual("wrong_destination", out["status"])
        self.assertEqual([], self.sent)


class HookUnits(unittest.TestCase):
    def setUp(self):
        self.sc = scrub.ValueScrubber()
        self.sc.register("AB12CD")

    def test_tool_result_hook_contract(self):
        hook = make_tool_result_hook(self.sc)
        self.assertEqual('{"v": "[CODE]"}', hook(tool_name="x", args={}, result='{"v": "AB12CD"}'))
        self.assertIsNone(hook(tool_name="x", args={}, result="nothing here"))
        self.assertIsNone(hook(tool_name="x", args={}, result={"_multimodal": True}))   # non-str: left alone
        self.assertIsNone(hook(tool_name="x", args={}, result=""))
        self.assertIsNone(hook(), "missing kwargs must not raise")

    def test_llm_output_hook_contract(self):
        hook = make_llm_output_hook(self.sc)
        self.assertEqual("code [CODE]", hook(response_text="code ab12cd", session_id="s"))
        self.assertIsNone(hook(response_text="clean"))
        self.assertIsNone(hook(response_text=None))

    def test_hooks_accept_any_extra_kwargs(self):
        hook = make_tool_result_hook(self.sc)
        self.assertEqual("[CODE]", hook(tool_name="x", args={}, result="AB12CD", duration_ms=3, status="ok", future_field=1))


if __name__ == "__main__":
    unittest.main()
