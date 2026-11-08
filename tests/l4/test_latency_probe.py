"""Round 9 send-and-measure: tools/coordinator/send_latency_probe.py (fake Twilio) and otp_delay.py --probe-nonce.

Nothing here touches the network or a phone: the sender gets a fake `ops` (req/balance), the reader a fake adb.
"""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path

from line.sms.otp import extract_otp

from .live import otp_delay
from .test_live_otp_delay import Clock, World

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO, "tools", "coordinator"))
import send_latency_probe as slp  # noqa: E402

_NUMBER_FIXTURE = tempfile.TemporaryDirectory()
_NUMBER_PATH = Path(_NUMBER_FIXTURE.name) / "personal_numbers.json"
_NUMBER_PATH.write_text(json.dumps({"owner": "+18475550101", "fi": "+17755550102"}))
_NUMBER_PATH.chmod(0o600)
slp.PERSONAL_NUMBERS_PATH = _NUMBER_PATH
slp.initialize_numbers()

FI = "+17755550102"
LINE = "+17755550166"            # fixture value shaped like the plugin line; never sent anywhere
NONCE = "kqzvtmrbxw"
SID = "SM" + "ab" * 16
SECRET = "fake-api-secret-do-not-print"


class FakeOps:
    """Stands in for twilio_ops: records every request; scripted replies."""

    API = "https://api.example.invalid/2010-04-01/Accounts/{AC}"
    STOP_BALANCE = 4.00

    def __init__(self, home, post=(201, None), statuses=("sent", "delivered"), balance=13.5, post_raises=None):
        self.HOME = home
        self.calls = []
        self.post = post
        self.statuses = list(statuses)
        self.bal = balance
        self.post_raises = post_raises

    def balance(self):
        self.calls.append(("BALANCE",))
        return 200, (self.bal, "USD")

    def req(self, method, url, data=None):
        self.calls.append((method, url, dict(data or {})))
        if method == "POST":
            if self.post_raises:
                raise self.post_raises
            status, body = self.post
            return status, body if body is not None else {
                "sid": SID, "status": "queued", "date_created": "Thu, 05 Nov 2026 21:00:00 +0000", "date_sent": None,
                "error_code": None, "num_segments": "1", "auth_hint": SECRET}
        st = self.statuses.pop(0) if self.statuses else "delivered"
        return 200, {"sid": SID, "status": st, "date_created": "Thu, 05 Nov 2026 21:00:00 +0000",
                     "date_sent": "Thu, 05 Nov 2026 21:00:01 +0000", "error_code": None}

    def posts(self):
        return [c for c in self.calls if c[0] == "POST"]


class Env:
    def __init__(self, line=LINE, **ops_kw):
        self.home = tempfile.mkdtemp()
        if line is not None:
            with open(os.path.join(self.home, "twilio_line_number"), "w") as fh:
                fh.write(line + "\n")
        self.out = tempfile.mkdtemp()
        self.ops = FakeOps(self.home, **ops_kw)
        self.t = [1_793_000_000.0]
        self.lines = []

    def now(self):
        return self.t[0]

    def sleep(self, s):
        self.t[0] += s

    def ready(self, nonce=NONCE, age_s=0.0, pid=None):
        with open(os.path.join(self.out, "latency-probe-%s-ready.json" % nonce), "w") as fh:
            json.dump({"nonce": nonce, "ready_ms": int((self.t[0] - age_s) * 1000), "pid": pid or os.getpid()}, fh)

    def send(self, nonce=NONCE, to=FI, **kw):
        kw.setdefault("wait_ready_s", 0)
        return slp.send(nonce, to, ops=self.ops, out_dir=self.out, now=self.now, sleep=self.sleep,
                        say=self.lines.append, **kw)

    def record(self, nonce=NONCE):
        with open(slp.record_path(self.out, nonce)) as fh:
            return json.load(fh)


class SenderRefuses(unittest.TestCase):
    def test_any_destination_but_the_fi_line_is_refused_before_any_request(self):
        for to in ("+18475550101", "+17755550172", "911", "+1911", "7755550102", "17755550102", "+1 775 555 0102",
                   "+1 (775) 555-0102", "tel:+17755550102", " +17755550102", "+17755550102\n", "+17755550102,911",
                   "＋17755550102", "+17755550103", "", None):
            e = Env()
            with self.assertRaises(slp.Refused, msg=repr(to)):
                e.send(to=to)
            self.assertEqual([], e.ops.calls, repr(to))
            self.assertEqual([], os.listdir(e.out), repr(to))

    def test_bad_nonces_are_refused(self):
        for n in ("123456", "abc", "a" * 17, "ABCDEFGH", "abcd1234", "abcdefg h", "abcdefgh\n", "", None, "kqzvtmrbxw;"):
            e = Env()
            with self.assertRaises(slp.Refused, msg=repr(n)):
                e.send(nonce=n)
            self.assertEqual([], e.ops.calls)

    def test_sender_must_be_the_stored_plugin_line(self):
        # None: no configured line file (missing config refuses). The rest: protected/personal or malformed values.
        for line in (None, "", "+18475550101", "+17755550172", "+17755550102", "+1775555016", "+177555501660", "7755550166"):
            e = Env(line=line)
            with self.assertRaises(slp.Refused, msg=repr(line)):
                e.send()
            self.assertEqual([], e.ops.posts())

    def test_unreadable_configured_line_refuses(self):
        e = Env(line=None)
        os.mkdir(os.path.join(e.home, "twilio_line_number"))   # present but unreadable as a file
        with self.assertRaises(slp.Refused):
            e.send()
        self.assertEqual([], e.ops.posts())

    def test_balance_floor(self):
        e = Env(balance=4.05)
        with self.assertRaises(slp.Refused):
            e.send()
        self.assertEqual([], e.ops.posts())
        self.assertEqual([], os.listdir(e.out))

    def test_cli_needs_the_coordinator_flag_to_send(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                slp.main(["--nonce", NONCE], say=lambda s: None)
            with self.assertRaises(SystemExit):
                slp.main([], say=lambda s: None)

    def test_cli_refusal_is_exit_2(self):
        lines = []
        self.assertEqual(2, slp.main(["--nonce", NONCE, "--to", "+18475550101", "--dry-run"], say=lines.append))
        self.assertTrue(lines[-1].startswith("REFUSED"))


class SenderSends(unittest.TestCase):
    def test_the_configured_line_is_the_sender(self):
        for line in (LINE, "+17755550177"):
            e = Env(line=line)
            self.assertEqual(0, e.send(follow_s=0))
            (post,) = e.ops.posts()
            self.assertEqual(line, post[2]["From"])

    def test_exactly_one_message_to_fi_with_the_probe_text_then_read_only_status(self):
        e = Env()
        self.assertEqual(0, e.send(follow_s=30, poll_s=3))
        (post,) = e.ops.posts()
        self.assertTrue(post[1].endswith("/Messages.json"))
        self.assertEqual({"To": FI, "From": LINE, "Body": "hermes-line latency probe " + NONCE}, post[2])
        gets = [c for c in e.ops.calls if c[0] == "GET"]
        self.assertTrue(gets)
        self.assertTrue(all(c[1].endswith("/Messages/%s.json" % SID) and not c[2] for c in gets))
        r = e.record()
        self.assertEqual("sent", r["state"])
        self.assertEqual("delivered", r["twilio"]["status"])
        self.assertEqual(SID, r["message_sid"])
        self.assertEqual(int(e.t[0] * 1000) >= r["request_ms"], True)
        self.assertIsNotNone(r["twilio"]["date_sent_ms"])
        self.assertIn("followed_s", r)
        self.assertEqual(["queued", "sent", "delivered"], [h["status"] for h in r["status_history"]])

    def test_numbers_masked_and_no_credentials_in_record_or_output(self):
        e = Env()
        e.send(follow_s=0)
        with open(slp.record_path(e.out, NONCE)) as fh:
            blob = fh.read() + "\n".join(e.lines)
        for raw in (FI, LINE, "7755550102", "7755550166", SECRET, "auth_hint"):
            self.assertNotIn(raw, blob)
        self.assertIn("+1775***0102", blob)
        self.assertIn("+1775***0166", blob)

    def test_same_nonce_twice_sends_once(self):
        e = Env()
        e.send(follow_s=0)
        e.t[0] += 3600                                  # even long after, the nonce stays spent
        with self.assertRaises(slp.Refused):
            e.send(follow_s=0)
        self.assertEqual(1, len(e.ops.posts()))

    def test_a_second_probe_within_15_minutes_is_refused(self):
        e = Env()
        e.send(follow_s=0)
        e.t[0] += 600
        with self.assertRaises(slp.Refused):
            e.send(nonce="bcdfghjkmn", follow_s=0)
        self.assertEqual(1, len(e.ops.posts()))
        e.t[0] += 400
        self.assertEqual(0, e.send(nonce="bcdfghjkmn", follow_s=0))
        self.assertEqual(2, len(e.ops.posts()))

    def test_twilio_refusal_is_recorded_and_not_retried(self):
        e = Env(post=(400, {"code": 30034, "message": "Message from an Unregistered Number", "more_info": "x"}))
        self.assertEqual(6, e.send(follow_s=30))
        self.assertEqual(1, len(e.ops.posts()))
        self.assertFalse([c for c in e.ops.calls if c[0] == "GET"])
        r = e.record()
        self.assertEqual("send_failed", r["state"])
        self.assertEqual(30034, r["twilio"]["error_code"])
        # a failed send does not block a corrected retry with a NEW nonce
        e.ops.post = (201, None)
        self.assertEqual(0, e.send(nonce="bcdfghjkmn", follow_s=0))

    def test_network_error_is_unknown_outcome_never_retried_and_blocks_new_probes(self):
        e = Env(post_raises=OSError("timed out"))
        self.assertEqual(6, e.send(follow_s=30))
        self.assertEqual(1, len(e.ops.posts()))
        self.assertEqual("unknown_outcome", e.record()["state"])
        e.ops.post_raises = None
        with self.assertRaises(slp.Refused):
            e.send(nonce="bcdfghjkmn")

    def test_undelivered_is_exit_6(self):
        e = Env(statuses=("undelivered",))
        self.assertEqual(6, e.send(follow_s=30))
        self.assertEqual(1, len(e.ops.posts()))

    def test_a_malformed_sid_is_never_put_in_a_url(self):
        e = Env(post=(201, {"sid": "SM../../Calls", "status": "queued"}))
        self.assertEqual(6, e.send(follow_s=30))
        self.assertFalse([c for c in e.ops.calls if c[0] == "GET"])

    def test_dry_run_makes_no_request_and_writes_nothing(self):
        e = Env()
        self.assertEqual(0, e.send(dry_run=True))
        self.assertEqual([], e.ops.calls)
        self.assertEqual([], os.listdir(e.out))
        self.assertIn("hermes-line latency probe " + NONCE, "\n".join(e.lines))

    def test_probe_text_never_looks_like_a_code(self):
        for _ in range(300):
            n = slp.new_nonce()
            self.assertRegex(n, r"^[a-z]{10}$")
            res = extract_otp(slp.body_for(n))
            self.assertIsNone(res.primary, n)
            self.assertFalse(res.ambiguous, n)
            self.assertFalse(re.search(r"\d", slp.body_for(n)))


class SenderWaitsForReader(unittest.TestCase):
    def test_no_reader_no_send(self):
        e = Env()
        with self.assertRaises(slp.Refused):
            e.send(wait_ready_s=10)
        self.assertEqual([], e.ops.posts())
        self.assertFalse(os.path.exists(slp.record_path(e.out, NONCE)))

    def test_stale_or_dead_or_other_nonce_reader_is_not_ready(self):
        for kw in ({"age_s": 16 * 60}, {"nonce": "bcdfghjkmn"}):
            e = Env()
            e.ready(**kw)
            with self.assertRaises(slp.Refused, msg=kw):
                e.send(wait_ready_s=5)
            self.assertEqual([], e.ops.posts())
        e = Env()
        e.ready()
        with self.assertRaises(slp.Refused):
            e.send(wait_ready_s=5, alive=lambda pid: False)
        self.assertEqual([], e.ops.posts())

    def test_ready_reader_gets_the_probe(self):
        e = Env()
        e.ready()
        self.assertEqual(0, e.send(wait_ready_s=5, follow_s=0))
        self.assertEqual(1, len(e.ops.posts()))


# ---------------------------------------------------------------------- the reader side

def probe_run(world, clock, out_dir, at_sleep, minutes=2, interval=2, linger=45, nonce=NONCE, **kw):
    """Run otp_delay in probe mode; `at_sleep(n)` is called after the n-th sleep (to send / deliver)."""
    state = {"n": 0}

    def sleep(s):
        clock.sleep(s)
        state["n"] += 1
        at_sleep(state["n"])

    out = io.StringIO()
    code = otp_delay.run(world.source(), minutes, interval, 240, out_dir, out, clock=clock, sleep=sleep,
                         probe_nonce=nonce, linger_s=linger, **kw)
    files = [f for f in os.listdir(out_dir) if f.startswith("otp-delay-")]
    with open(os.path.join(out_dir, files[0])) as fh:
        doc = json.load(fh)
    return code, out.getvalue(), doc


class ReaderProbe(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.w = World(self.clock)                   # its `adb shell date` runs 2 s ahead of the Mac clock
        self.w.add(1, "old message", minute_ago=30)
        self.out = tempfile.mkdtemp()
        self.env = Env()
        self.env.out = self.out
        self.env.now = self.clock

    def _deliver(self, n_send=1, n_arrive=2, body=None, address="+17755550166"):
        """Send through the REAL send helper (fake Twilio) at sleep n_send; the phone gets it at sleep n_arrive."""
        body = body or "hermes-line latency probe " + NONCE

        def at(n):
            if n == n_send:
                self.assertEqual(0, slp.send(NONCE, FI, ops=self.env.ops, out_dir=self.out, follow_s=0,
                                             wait_ready_s=5, now=self.clock, sleep=self.clock.sleep, say=lambda s: None))
            if n == n_arrive:
                self.w.add(7, body, address=address, date_sent_offset_ms=-1500)
        return at

    def test_send_phone_and_plugin_times_are_joined(self):
        code, text, doc = probe_run(self.w, self.clock, self.out, self._deliver())
        self.assertEqual(0, code)
        p = doc["probe"]
        self.assertTrue(p["seen"])
        self.assertEqual("7", p["message_id"])
        self.assertEqual("[TEXT] " + NONCE, p["body_masked"])
        self.assertTrue(p["template_exact"])
        self.assertFalse(p["in_baseline"])
        self.assertTrue(p["started_before_send"])
        self.assertEqual("sent", p["send"]["state"])
        self.assertEqual(SID, p["send"]["message_sid"])
        for k in ("send_to_phone_s", "phone_to_plugin_s", "send_to_plugin_s", "read_resolution_s"):
            self.assertIsNotNone(p[k], k)
        # send->plugin is pure Mac clock and equals send->phone + phone->plugin
        self.assertAlmostEqual(p["send_to_plugin_s"], p["send_to_phone_s"] + p["phone_to_plugin_s"], places=2)
        self.assertGreater(p["send_to_plugin_s"], 0)
        self.assertLess(p["send_to_plugin_s"], 10)
        self.assertTrue(1.0 <= p["clock_skew_s_estimate"] <= 3.0)    # the fake phone runs 2 s ahead; 1 s resolution
        self.assertIsNotNone(p["phone_date_sent_utc"])
        for k in ("phone_date_ms", "first_seen_ms", "previous_poll_start_ms", "run_first_poll_ms"):
            self.assertNotIn(k, p)
        self.assertIn("PROBE id=7 body=[TEXT] %s exact_template=True" % NONCE, text)
        self.assertEqual("ok", doc["status"])
        self.assertTrue(doc["messages"][0]["probe"])

    def test_the_run_stops_once_the_send_record_is_final(self):
        code, _, doc = probe_run(self.w, self.clock, self.out, self._deliver(), minutes=10)
        self.assertEqual(0, code)
        self.assertLess(doc["polls"], 10)

    def test_without_a_final_record_it_lingers_then_stops(self):
        def at(n):
            if n == 2:
                self.w.add(7, "hermes-line latency probe " + NONCE)
        code, _, doc = probe_run(self.w, self.clock, self.out, at, minutes=10, interval=2, linger=10)
        self.assertEqual(0, code)
        self.assertIsNone(doc["probe"]["send"])
        self.assertIsNone(doc["probe"]["send_to_plugin_s"])
        self.assertIsNotNone(doc["probe"]["phone_to_plugin_s"])
        self.assertLess(doc["polls"], 12)

    def test_the_body_is_masked_except_the_nonce(self):
        body = "Sent from your Twilio trial account - hermes-line latency probe %s . Reply STOP" % NONCE
        code, text, doc = probe_run(self.w, self.clock, self.out, self._deliver(body=body))
        self.assertEqual(0, code)
        self.assertEqual("[TEXT] %s [TEXT]" % NONCE, doc["probe"]["body_masked"])
        self.assertFalse(doc["probe"]["template_exact"])
        self.assertEqual("hermes-line latency probe " + NONCE, doc["probe"].pop("template"))   # what was SENT
        blob = text + json.dumps(doc)
        for words in ("Twilio trial", "Reply STOP", "latency probe " + NONCE, "7755550166"):
            self.assertNotIn(words, blob)

    def test_the_nonce_must_be_a_whole_token(self):
        def at(n):
            if n == 1:
                self.w.add(7, "hermes-line latency probe x%sy" % NONCE)
        code, text, doc = probe_run(self.w, self.clock, self.out, at, minutes=0.5)
        self.assertEqual(6, code)
        self.assertFalse(doc["probe"]["seen"])
        self.assertEqual("probe not seen", doc["status"])
        self.assertNotIn("probe x", text + json.dumps(doc))

    def test_ready_file_after_the_baseline_and_removed_at_the_end(self):
        ready = otp_delay.ready_path(self.out, NONCE)
        seen = []

        def at(n):
            seen.append(os.path.exists(ready))
        probe_run(self.w, self.clock, self.out, at, minutes=0.2)
        self.assertTrue(seen and all(seen))
        self.assertFalse(os.path.exists(ready))

    def test_no_ready_file_when_the_phone_is_not_there(self):
        w = World(self.clock, state=None)
        out = io.StringIO()
        code = otp_delay.run(w.source(), 1, 2, 240, self.out, out, clock=self.clock, sleep=self.clock.sleep,
                             probe_nonce=NONCE)
        self.assertEqual(3, code)
        self.assertFalse(os.path.exists(otp_delay.ready_path(self.out, NONCE)))
        self.assertIn("the send helper will not send", out.getvalue())

    def test_a_probe_already_in_the_inbox_is_found_and_flagged(self):
        self.env.ops.calls.clear()
        slp.send(NONCE, FI, ops=self.env.ops, out_dir=self.out, follow_s=0, wait_ready_s=0, now=self.clock,
                 sleep=self.clock.sleep, say=lambda s: None)
        self.clock.sleep(3)
        self.w.add(7, "hermes-line latency probe " + NONCE)
        code, _, doc = probe_run(self.w, self.clock, self.out, lambda n: None, minutes=0.5)
        self.assertEqual(0, code)
        self.assertTrue(doc["probe"]["in_baseline"])
        self.assertFalse(doc["probe"]["started_before_send"])

    def test_a_failed_send_stops_the_wait(self):
        self.env.ops.post = (400, {"code": 30034, "message": "unregistered"})

        def at(n):
            if n == 1:
                slp.send(NONCE, FI, ops=self.env.ops, out_dir=self.out, follow_s=0, wait_ready_s=5, now=self.clock,
                         sleep=self.clock.sleep, say=lambda s: None)
        code, text, doc = probe_run(self.w, self.clock, self.out, at, minutes=10)
        self.assertEqual(6, code)
        self.assertLess(doc["polls"], 5)
        self.assertIn("not delivered (30034)", text)
        self.assertEqual("send_failed", doc["probe"]["send"]["state"])

    def test_drops_during_a_probe_run_are_still_logged(self):
        def at(n):
            if n == 1:
                self.w.fake.state = "offline"
            if n == 2:
                self.w.fake.state = "device"
            self._deliver(n_send=3, n_arrive=4)(n)
        code, _, doc = probe_run(self.w, self.clock, self.out, at, minutes=2)
        self.assertEqual(0, code)
        self.assertEqual(1, doc["reachability"]["drops"])
        self.assertTrue(doc["probe"]["seen"])

    def test_bad_nonce_is_refused(self):
        with self.assertRaises(ValueError):
            otp_delay.run(self.w.source(), 1, 2, 240, self.out, io.StringIO(), clock=self.clock,
                          sleep=self.clock.sleep, probe_nonce="123456")
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                otp_delay.main(["--probe-nonce", "ABC123"])
            with self.assertRaises(SystemExit):
                otp_delay.main(["--dry-run", "--linger", "-1"])

    def test_without_a_nonce_the_r8_output_is_unchanged(self):
        out = io.StringIO()
        otp_delay.run(self.w.source(), 0.2, 2, 240, self.out, out, clock=self.clock, sleep=self.clock.sleep)
        with open(os.path.join(self.out, os.listdir(self.out)[0])) as fh:
            doc = json.load(fh)
        self.assertNotIn("probe", doc)
        self.assertEqual(["otp-delay-"], sorted({f[:10] for f in os.listdir(self.out)}))


class ReaderDryRun(unittest.TestCase):
    def test_dry_run_end_to_end(self):
        d = tempfile.mkdtemp()
        out = io.StringIO()
        self.assertEqual(0, otp_delay.main(["--dry-run", "--out-dir", d], out=out))
        text = out.getvalue()
        self.assertIn("DRY RUN", text)
        (f,) = [x for x in os.listdir(d) if x.startswith("otp-delay-dry-")]
        with open(os.path.join(d, f)) as fh:
            doc = json.load(fh)
        self.assertTrue(doc["dry_run"])
        p = doc["probe"]
        self.assertTrue(p["seen"])
        self.assertTrue(p["send"]["dry_run"])
        self.assertEqual("[TEXT] " + otp_delay.DRY_NONCE, p["body_masked"])
        self.assertEqual(1, doc["reachability"]["drops"])
        self.assertGreater(p["send_to_plugin_s"], 0)
        self.assertFalse(os.path.exists(otp_delay.ready_path(d, otp_delay.DRY_NONCE)))

    def test_dry_run_defaults_to_a_temporary_directory(self):
        before = set(os.listdir(otp_delay.DEFAULT_OUT)) if os.path.isdir(otp_delay.DEFAULT_OUT) else set()
        out = io.StringIO()
        self.assertEqual(0, otp_delay.main(["--dry-run", "--probe-nonce", NONCE], out=out))
        after = set(os.listdir(otp_delay.DEFAULT_OUT)) if os.path.isdir(otp_delay.DEFAULT_OUT) else set()
        self.assertEqual(before, after)

    def test_the_reader_never_unwraps_a_body(self):
        with open(otp_delay.__file__, encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn(".reveal()", src)
        self.assertNotIn("._v", src)


if __name__ == "__main__":
    unittest.main()
