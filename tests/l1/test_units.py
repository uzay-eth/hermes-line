"""Unit tests: store, masking, lease, signatures, TwiML, config, grants, released OTPs, driver."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import harness  # noqa: F401  (puts the repo root on sys.path)
from plugin.line.engine.config import EngineConfig
from plugin.line.engine.contracts import CallState, Direction, Mode, Origin
from plugin.line.engine.driver import INTERRUPTED_SUFFIX, HermesAgentDriver, reconcile_history
from plugin.line.engine.lease import Lease, LeaseHeld
from plugin.line.engine.redact import mask_digits, mask_number, mask_text
from plugin.line.engine.service import GrantBook
from plugin.line.policy import ConsentStore
from plugin.line.server import twilio_signature, twiml
from plugin.line.store import StaleRevision, Store
from plugin.line.store.released_otps import ReleasedOtps


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "line.db")

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def _call(self):
        task = self.store.create_task(title="Book", goal="Book a table", destination_e164=harness.BUSINESS,
                                      destination_masked=mask_number(harness.BUSINESS), mode=Mode.AGENT,
                                      origin=Origin())
        return task, self.store.create_call(task_id=task.id, direction=Direction.OUTBOUND,
                                            counterpart_e164=harness.BUSINESS, counterpart_masked="x",
                                            state=CallState.AWAITING_APPROVAL, mode=Mode.AGENT)

    def test_revision_is_global_and_monotonic(self):
        _, call = self._call()
        r1 = self.store.revision()
        self.store.append_transcript(call.id, "business", "hello")
        r2 = self.store.revision()
        self.store.update_call(call.id, state=CallState.DIALING)
        self.assertLess(r1, r2)
        self.assertLess(r2, self.store.revision())

    def test_control_revision_moves_only_on_state_or_mode(self):
        _, call = self._call()
        rev = self.store.update_call(call.id, state=CallState.DIALING)
        self.store.append_transcript(call.id, "business", "please hold")
        current = self.store.get_call(call.id)
        self.assertEqual(current.control_revision, rev)
        self.assertGreater(current.revision, rev)
        self.assertIsNotNone(current.state_since)

    def test_stale_command_refused_and_idempotent_repeat(self):
        _, call = self._call()
        seen = self.store.revision()
        self.store.update_call(call.id, state=CallState.DIALING)
        with self.assertRaises(StaleRevision):
            self.store.enqueue_command(idempotency_key="k-stale-1", action="hang_up", source="desktop",
                                       call_id=call.id, expected_revision=seen)
        fresh = self.store.revision()
        row, created = self.store.enqueue_command(idempotency_key="k-ok-0001", action="hang_up", source="desktop",
                                                  call_id=call.id, expected_revision=fresh)
        again, created2 = self.store.enqueue_command(idempotency_key="k-ok-0001", action="hang_up",
                                                     source="desktop", call_id=call.id, expected_revision=0)
        self.assertTrue(created)
        self.assertFalse(created2)
        self.assertEqual(row["id"], again["id"])

    def test_transcript_and_summary_are_masked_on_write(self):
        task, call = self._call()
        self.store.append_transcript(call.id, "business", "your verification code is 482913")
        self.store.update_call(call.id, summary="card 4111 1111 1111 1111")
        self.store.update_task(task.id, summary="the code is 5521")
        self.assertNotIn("482913", json.dumps(self.store.transcript(call.id)))
        self.assertNotIn("4111", self.store.get_call(call.id).summary)
        self.assertNotIn("5521", self.store.get_task(task.id).summary)

    def test_relay_token_single_use_and_expiry(self):
        _, call = self._call()
        self.store.put_relay_token("h1", call.id, 60)
        self.assertEqual(self.store.take_relay_token("h1"), call.id)
        self.assertIsNone(self.store.take_relay_token("h1"))
        self.store.put_relay_token("h2", call.id, -1)
        self.assertIsNone(self.store.take_relay_token("h2"))

    def test_outbox_one_summary_per_call(self):
        _, call = self._call()
        self.assertTrue(self.store.outbox_put(task_id=None, call_id=call.id, kind="summary", text="a"))
        self.assertFalse(self.store.outbox_put(task_id=None, call_id=call.id, kind="summary", text="b"))
        row = self.store.outbox_pending()[0]
        for _ in range(5):
            self.store.outbox_mark(row["id"], delivered=False, error="no_route")
        self.assertEqual(self.store.outbox_pending(), [])

    def test_released_otps_persist_across_reopen(self):
        rel = ReleasedOtps(self.store)
        rel.add("msg-1|sender|123")
        self.assertIn("msg-1|sender|123", rel)
        self.assertFalse(rel.contains("msg-2"))
        path = self.store.path
        self.store.close()
        self.store = Store(path)
        self.assertTrue(ReleasedOtps(self.store).contains("msg-1|sender|123"))
        raw = self.store._conn.execute("SELECT key_hash FROM released_otps").fetchall()
        self.assertNotIn("msg-1", json.dumps([tuple(r) for r in raw]))


class MaskingTests(unittest.TestCase):
    def test_contract_tokens(self):
        self.assertEqual(mask_text("Your verification code is 482913."), "Your verification code is [CODE].")
        self.assertEqual(mask_text("card 4111-1111-1111-1111"), "card [CARD]")
        self.assertEqual(mask_text("ssn 123-45-6789"), "ssn [NUMBER]")
        self.assertIn("[CODE]", mask_text("the pin is four eight two nine"))
        self.assertIn("[SECRET]", mask_text("key SK" + "a" * 32))

    def test_ordinary_speech_stays_readable(self):
        for text in ("For reservations press 1.", "Table for 4 at 7 pm on Friday the 14th.",
                     "Your confirmation number is 48213.", "Call us back at 775 555 0101."):
            self.assertEqual(mask_text(text), text)

    def test_numbers_and_digits(self):
        self.assertEqual(mask_number("+17755550101"), "+1 775-•••-0101")
        self.assertEqual(mask_digits("1"), "1")
        self.assertEqual(mask_digits("123456"), "[6 digits]")


_HOLDER = """
import sys, time
sys.path.insert(0, {root!r})
from plugin.line.store import Store
from plugin.line.engine.lease import Lease
s = Store({db!r})
l = Lease({locks!r}, "default", s)
l.acquire(); l.mark("ready")
print("held", flush=True)
time.sleep(60)
"""


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = Path(self.dir.name) / "line.db"
        self.store = Store(self.db)

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_single_owner_and_clean_release(self):
        a = Lease(Path(self.dir.name) / "locks", "default", self.store)
        b = Lease(Path(self.dir.name) / "locks", "default", self.store)
        a.acquire()
        with self.assertRaises(LeaseHeld) as ctx:
            b.acquire()
        self.assertEqual(ctx.exception.holder["owner_id"], a.owner_id)
        a.release()
        b.acquire()
        self.assertFalse(b.previous_crashed())
        b.release()

    def test_killed_owner_releases_lock_and_is_detected(self):
        root = str(Path(__file__).resolve().parents[2])
        code = _HOLDER.format(root=root, db=str(self.db), locks=str(Path(self.dir.name) / "locks"))
        proc = subprocess.Popen([sys.executable, "-B", "-c", code], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(), "held")
            mine = Lease(Path(self.dir.name) / "locks", "default", self.store)
            with self.assertRaises(LeaseHeld):
                mine.acquire()
            proc.send_signal(signal.SIGKILL)
            proc.wait(10)
            mine.acquire()
            self.assertTrue(mine.previous_crashed())
            mine.release()
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.stdout.close()


class SignatureTests(unittest.TestCase):
    def test_twilio_documentation_vector(self):
        # Worked example from Twilio's request-validation documentation.
        params = [("CallSid", "CA1234567890ABCDE"), ("Caller", "+12349013030"), ("Digits", "1234"),
                  ("From", "+12349013030"), ("To", "+18005551212")]
        url = "https://mycompany.com/myapp.php?foo=1&bar=2"
        self.assertEqual(twilio_signature.compute("12345", url, params), "0/KCTR6DLpKmkAf8muzZqo1nDgQ=")
        self.assertTrue(twilio_signature.valid("12345", url, params, "0/KCTR6DLpKmkAf8muzZqo1nDgQ="))

    def test_rejections_and_port_variant(self):
        sig = twilio_signature.compute("tok", "https://a.test/x", [("A", "1")])
        self.assertFalse(twilio_signature.valid("tok", "https://a.test/x", [("A", "2")], sig))
        self.assertFalse(twilio_signature.valid("other", "https://a.test/x", [("A", "1")], sig))
        self.assertFalse(twilio_signature.valid(None, "https://a.test/x", [("A", "1")], sig))
        self.assertFalse(twilio_signature.valid("tok", "https://a.test/x", [("A", "1")], None))
        self.assertTrue(twilio_signature.valid("tok", "https://a.test:443/x", [("A", "1")], sig))


class TwimlAndConfigTests(unittest.TestCase):
    def test_twiml_escapes_and_never_records(self):
        body = twiml.response(twiml.conversation_relay(ws_url="wss://h/r?t=a&b=1", action_url="https://h/a",
                                                       call_id="c1", welcome='Hi <"you">'))
        import xml.etree.ElementTree as ET
        relay = ET.fromstring(body.split("?>", 1)[1]).find("./Connect/ConversationRelay")
        self.assertEqual(relay.get("url"), "wss://h/r?t=a&b=1")
        self.assertEqual(relay.get("welcomeGreeting"), 'Hi <"you">')
        self.assertNotIn("record=\"true\"", body.lower())
        conf = twiml.conference(name="hl-c1", status_url="https://h/s")
        self.assertIn('record="do-not-record"', conf)
        self.assertIn('beep="false"', conf)

    def test_config_validation(self):
        with self.assertRaises(ValueError):
            EngineConfig.load({"public_base_url": "http://insecure.test"})
        with self.assertRaises(ValueError):
            EngineConfig.load({"owner_number": "775-555-0199"})
        with self.assertRaises(ValueError):
            EngineConfig.load({"unknown_key": 1})
        with self.assertRaises(ValueError):
            EngineConfig.load({"max_call_minutes": 0})
        cfg = EngineConfig.load({"public_base_url": "https://line.test/"})
        self.assertEqual(cfg.public_base_url, "https://line.test")
        self.assertEqual(cfg.missing_for_live(), ["from_number"])


class GrantTests(unittest.TestCase):
    def test_rolling_renewal_keeps_single_use_and_lifetime(self):
        now = [0.0]
        consent = ConsentStore(clock=lambda: now[0])
        book = GrantBook(consent, clock=lambda: now[0])
        binding = {"destination": harness.OWNER}
        book.put("bridge_owner", "t1", "c1", binding, 3600, 7200)
        first = book.peek("bridge_owner", "t1", "c1")
        now[0] = 3100.0
        self.assertEqual(book.renew(), 1)
        second = book.peek("bridge_owner", "t1", "c1")
        self.assertNotEqual(first, second)
        self.assertFalse(consent.consume(first, "bridge_owner", "t1", binding))  # old one is spent
        now[0] = 3700.0  # past the first token's TTL: the renewed one still works
        token = book.take("bridge_owner", "t1", "c1")
        self.assertTrue(consent.consume(token, "bridge_owner", "t1", binding))
        self.assertFalse(consent.consume(token, "bridge_owner", "t1", binding))

    def test_lifetime_ends_renewal_and_used_grant_is_not_revived(self):
        now = [0.0]
        consent = ConsentStore(clock=lambda: now[0])
        book = GrantBook(consent, clock=lambda: now[0])
        book.put("bridge_owner", "t1", "c1", {"d": 1}, 3600, 3700)
        token = book.peek("bridge_owner", "t1", "c1")
        self.assertTrue(consent.consume(token, "bridge_owner", "t1", {"d": 1}))
        now[0] = 3100.0
        self.assertEqual(book.renew(), 0)  # consume of the used token fails: dropped
        self.assertIsNone(book.peek("bridge_owner", "t1", "c1"))

    def test_otp_grants_never_renew(self):
        now = [0.0]
        consent = ConsentStore(clock=lambda: now[0])
        book = GrantBook(consent, clock=lambda: now[0])
        book.put("otp_release", "t1", "otp", {"s": 1}, 600, 600, renewable=False, meta={"kind": "call", "call_id": "c"})
        token = book.peek("otp_release", "t1", "otp")
        self.assertEqual(book.renew(), 0)
        self.assertEqual(book.peek("otp_release", "t1", "otp"), token)
        self.assertEqual(book.find("otp_release", kind="call", call_id="c"), ["t1"])
        now[0] = 601.0
        self.assertIsNone(book.peek("otp_release", "t1", "otp"))


class FakeAgent:
    """Stands in for run_agent.AIAgent: streams chunks, honours interrupt()."""

    def __init__(self, chunks, delay=0.0, stream=True):
        self.chunks, self.delay, self.stream = chunks, delay, stream
        self.stop = threading.Event()
        self.histories = []
        self.closed = False

    def run_conversation(self, text, conversation_history=None, stream_callback=None):
        self.histories.append(list(conversation_history or []))
        self.stop.clear()
        out = ""
        for chunk in self.chunks:
            if self.stop.is_set():
                break
            out += chunk
            if self.stream and stream_callback:
                stream_callback(chunk)
            time.sleep(self.delay)
        msgs = list(conversation_history or []) + [{"role": "user", "content": text},
                                                   {"role": "assistant", "content": out}]
        return {"final_response": out, "messages": msgs}

    def interrupt(self):
        self.stop.set()

    def close(self):
        self.closed = True


class DriverTests(unittest.TestCase):
    def _driver(self, agent):
        d = HermesAgentDriver(lambda briefing, sid: agent, "s1")
        d.start("briefing")
        return d

    def test_stream_and_history_carry_forward(self):
        agent = FakeAgent(["Hello ", "there."])
        d = self._driver(agent)
        got = []
        res = d.turn("[business] hi", got.append)
        self.assertEqual("".join(got), "Hello there.")
        self.assertFalse(res.interrupted)
        self.assertIsNotNone(res.first_text_ms)
        d.turn("again", got.append)
        self.assertEqual(agent.histories[1][-1]["content"], "Hello there.")

    def test_barge_in_drops_stale_chunks_and_reconciles(self):
        agent = FakeAgent(["One. ", "Two. ", "Three. ", "Four."], delay=0.05)
        d = self._driver(agent)
        got = []
        first_chunk, interrupted = threading.Event(), threading.Event()

        def emit(chunk):  # deterministic: the barge-in lands right after the first chunk, never after a race
            got.append(chunk)
            if not first_chunk.is_set():
                first_chunk.set()
                assert interrupted.wait(5), "interrupt was not issued"

        t = threading.Thread(target=lambda: got.append(d.turn("hi", emit)))
        t.start()
        self.assertTrue(first_chunk.wait(5))
        d.interrupt("One.")
        interrupted.set()
        t.join(5)
        self.assertFalse(t.is_alive())
        result = got[-1]
        chunks = got[:-1]
        self.assertTrue(result.interrupted)
        self.assertLessEqual(len(chunks), 2)
        last = d.history[-1]
        self.assertEqual(last["content"], "One." + INTERRUPTED_SUFFIX)

    def test_interrupt_after_turn_rewrites_history(self):
        d = self._driver(FakeAgent(["A long sentence that was cut."]))
        d.turn("hi", lambda c: None)
        d.interrupt("A long")
        self.assertEqual(d.history[-1]["content"], "A long" + INTERRUPTED_SUFFIX)

    def test_non_streaming_provider_delivers_once(self):
        d = self._driver(FakeAgent(["Whole answer."], stream=False))
        got = []
        d.turn("hi", got.append)
        self.assertEqual(got, ["Whole answer."])

    def test_ask_is_stateless_and_bounded(self):
        agent = FakeAgent(["x"] * 50, delay=0.05)
        d = self._driver(agent)
        start = time.monotonic()
        self.assertEqual(d.ask("classify", timeout_s=0.2), "")
        self.assertLess(time.monotonic() - start, 2.0)
        self.assertEqual(d.history, [])

    def test_reconcile_helper(self):
        msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "full"}]
        self.assertEqual(reconcile_history(msgs, "")[-1]["content"], INTERRUPTED_SUFFIX.strip())
        self.assertEqual(msgs[-1]["content"], "full")  # input untouched

    def test_close(self):
        agent = FakeAgent(["x"])
        d = self._driver(agent)
        d.close()
        self.assertTrue(agent.closed)


if __name__ == "__main__":
    unittest.main()
