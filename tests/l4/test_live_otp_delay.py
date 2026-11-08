"""tests/l4/live/otp_delay.py against a fake adb: masked output only, lags, hold detection, drops, owner-step exits."""
import io
import json
import os
import shlex
import tempfile
import unittest

from line.sms import AdbSource
from line.sms.sources.adb import MDNS_SUFFIX, RunResult

from .helpers import FakeAdb, T0, load_otp_cases
from .live import otp_delay

CODE = "739204"
NAME = "adb-5B050DLCQ000P3-eQkDjp"
ADDR = "192.168.1.32:34897"


class Clock:
    def __init__(self, t=T0 / 1000.0 + 3600):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class World:
    """A fake phone: `inbox` rows are visible; `hidden` rows become visible at a given Mac time (Android 17 hold)."""

    def __init__(self, clock, props=None, **adb_kw):
        self.clock = clock
        self.fake = FakeAdb([], props=props, **adb_kw)
        self.hidden = []
        self.log = []
        self.fetch_cost_s = 0.05
        self.on_call = None

    def add(self, i, body, minute_ago=0, address="ACME", visible_after_s=0.0, date_sent_offset_ms=-800):
        now_ms = int(self.clock() * 1000)
        row = {"_id": str(i), "address": address, "date": now_ms - int(minute_ago * 60000), "type": 1, "body": body}
        row["date_sent"] = row["date"] + date_sent_offset_ms
        if visible_after_s > 0:
            self.hidden.append((self.clock() + visible_after_s, row))
        else:
            self.fake.rows.insert(0, row)

    def __call__(self, argv, timeout):
        self.clock.t += self.fetch_cost_s
        for at, row in list(self.hidden):
            if self.clock() >= at:
                self.hidden.remove((at, row))
                self.fake.rows.insert(0, row)
        self.log.append(list(argv))
        if self.on_call:
            self.on_call(self)
        if argv[-1] == "date +%s":
            return RunResult(0, "%d\n" % int(self.clock() + 2), "")
        return self.fake(argv, timeout)

    def source(self, serial=None, **kw):
        return AdbSource(serial=serial, adb_path="adb", runner=self, clock=self.clock, **kw)

    def remote_commands(self):
        return [a[a.index("shell") + 1] for a in self.log if "shell" in a]


def run(world, minutes=2, interval=5, serial=None, sleep=None, **kw):
    out = io.StringIO()
    d = tempfile.mkdtemp()
    code = otp_delay.run(world.source(serial), minutes, interval, 240, d, out, clock=world.clock,
                         sleep=sleep or world.clock.sleep, **kw)
    files = [f for f in os.listdir(d) if f.startswith("otp-delay-") and f.endswith(".json")]
    doc = json.load(open(os.path.join(d, files[0]))) if files else None
    return code, out.getvalue(), doc, d


def arrive_at_poll(world, n, *msgs):
    """A sleep function that adds messages after the n-th sleep."""
    state = {"n": 0}
    orig = world.clock.sleep

    def sleep(s):
        orig(s)
        state["n"] += 1
        if state["n"] == n:
            for args, kw in msgs:
                world.add(*args, **kw)

    return sleep


class Basics(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.w = World(self.clock)
        self.w.add(1, "old message from last week", minute_ago=60)
        self.w.add(2, "Your ACME code is %s." % CODE, minute_ago=30)

    def test_baseline_messages_are_not_reported(self):
        code, text, doc, _ = run(self.w)
        self.assertEqual(0, code)
        self.assertEqual([], doc["messages"])
        self.assertIn("baseline 2 message(s)", text)
        self.assertEqual("ok", doc["status"])

    def test_a_new_code_message_gets_lags_and_flags(self):
        body = "Your ACME code is %s. Do not share it." % CODE
        code, text, doc, _ = run(self.w, minutes=1, interval=5, sleep=arrive_at_poll(self.w, 2, ((3, body), {})))
        self.assertEqual(0, code)
        (m,) = doc["messages"]
        self.assertEqual("3", m["id"])
        self.assertEqual("A***ME", m["from"])
        self.assertEqual("yes", m["code_like"])
        self.assertGreaterEqual(m["lag_s"], 0)
        self.assertLess(m["lag_s"], 15)
        self.assertAlmostEqual(m["date_sent_lag_s"] - m["lag_s"], 0.8, places=1)
        self.assertGreater(m["fetch_ms"], 0)
        self.assertGreater(m["meta_query_ms"], 0)
        self.assertEqual(len(body), m["body_len"])
        self.assertNotIn("_date_ms", m)
        self.assertEqual(doc["reachability"]["ok_polls"], doc["plugin_read_latency_ms"]["n"])
        self.assertEqual(doc["polls"], len(doc["fetch_ms_all"]))
        self.assertIn("MSG id=3 from=A***ME code_like=yes", text)

    def test_nothing_unmasked_in_output_or_json(self):
        cases = [c for c in load_otp_cases() if c["codes"]]
        for case in cases:
            clock = Clock()
            w = World(clock)
            w.add(1, "baseline", minute_ago=5)
            out, d = io.StringIO(), tempfile.mkdtemp()
            otp_delay.run(w.source(), 0.5, 5, 240, d, out, clock=clock, sleep=arrive_at_poll(w, 1, ((9, case["body"]), {})))
            blob = out.getvalue() + open(os.path.join(d, os.listdir(d)[0])).read()
            for c in case["codes"]:
                self.assertNotIn(c, blob, case["name"])
            if len(case["body"]) >= 12:
                self.assertNotIn(case["body"], blob, case["name"])
            self.assertIn('"bodies_or_codes_recorded": false', blob)

    def test_plain_message_is_not_code_like(self):
        sleep = arrive_at_poll(self.w, 1, ((4, "Hi, are we still on for lunch tomorrow?"), {"address": "+15550100123"}))
        (m,) = run(self.w, minutes=0.5, sleep=sleep)[2]["messages"]
        self.assertEqual("no", m["code_like"])
        self.assertEqual("+***23", m["from"])

    def test_android17_hold_shows_as_a_large_lag(self):
        """The provider withholds the OTP message for 3 min (stand-in for 3 h): it appears late, lag measured from `date`."""
        sleep = arrive_at_poll(self.w, 1, ((5, "Your ACME code is %s." % CODE), {"visible_after_s": 180}), ((6, "plain text"), {}))
        code, text, doc, _ = run(self.w, minutes=6, interval=10, sleep=sleep)
        by = {m["id"]: m for m in doc["messages"]}
        self.assertLess(by["6"]["lag_s"], 20)
        self.assertGreater(by["5"]["lag_s"], 170)
        self.assertEqual("yes", by["5"]["code_like"])
        self.assertIn("Android 17 OTP hold likely", doc["verdict_hint"])
        self.assertEqual(0, code)

    def test_a_poll_is_one_plugin_fetch_and_extra_queries_only_for_new_messages(self):
        """Latency: no devices/getprop per poll, and the date_sent metadata query runs once per new message."""
        code, text, doc, _ = run(self.w, minutes=1, interval=10)
        polls = doc["polls"]
        queries = [c for c in self.w.remote_commands() if c.startswith("content")]
        access = [c for c in queries if "_id<0" in c]
        reads = [c for c in queries if "length(body)" in c]
        self.assertEqual(polls, len(reads))
        self.assertEqual(1, len(access))
        self.assertEqual(len(queries), len(access) + len(reads), "no per-poll metadata query")
        self.assertFalse([a for a in self.w.log if a[1:2] == ["devices"]])

    def test_every_remote_command_is_read_only(self):
        sleep = arrive_at_poll(self.w, 1, ((7, "Your ACME code is %s." % CODE), {}))
        run(self.w, minutes=0.5, sleep=sleep)
        for argv in self.w.log:
            rest = argv[1:]
            if rest[:1] == ["-s"]:
                rest = rest[2:]
            if rest[0] == "shell":
                self.assertTrue(rest[1].startswith(("content query", "getprop", "date +%s")), rest[1])
            else:
                self.assertIn(rest[0], ("get-serialno", "mdns", "connect"))
        queries = [shlex.split(c) for c in self.w.remote_commands() if c.startswith("content")]
        meta = [q for q in queries if q[q.index("--projection") + 1] == "_id:date:date_sent"]
        self.assertTrue(meta, "the date_sent metadata query ran")
        for q in meta:
            self.assertNotIn("body", q[q.index("--projection") + 1])
        for q in queries:
            for bad in ("insert", "update", "delete"):
                self.assertNotIn(bad, q)

    def test_phone_clock_skew_is_estimated_and_applied(self):
        code, text, doc, _ = run(self.w)
        self.assertAlmostEqual(doc["clock_skew_s_estimate"], 2.0, delta=1.1)
        self.assertTrue(doc["android17_otp_protection_expected"])
        self.assertEqual("17", doc["device"]["android"])
        self.assertEqual("usb", doc["device"]["transport"])


class Drops(unittest.TestCase):
    """Over Wi-Fi the link drops; the run logs each drop and reconnect with times and keeps watching."""

    def test_usb_style_drop_is_logged_and_the_run_survives(self):
        clock = Clock()
        w = World(clock)
        w.add(1, "baseline", minute_ago=5)
        state = {"polls": 0}
        orig_sleep = clock.sleep

        def sleep(s):
            orig_sleep(s)
            state["polls"] += 1
            if state["polls"] == 2:
                w.fake.state = "offline"
            if state["polls"] == 5:
                w.fake.state = "device"
                w.add(8, "Your ACME code is %s." % CODE)

        code, text, doc, _ = run(w, minutes=2, interval=5, sleep=sleep)
        self.assertEqual(0, code)
        kinds = [e["event"] for e in doc["events"]]
        self.assertEqual(["drop", "recovered"], [k for k in kinds if k in ("drop", "recovered")])
        drop = [e for e in doc["events"] if e["event"] == "drop"][0]
        rec = [e for e in doc["events"] if e["event"] == "recovered"][0]
        self.assertIn("offline", drop["detail"])
        self.assertGreater(rec["down_s"], 5)
        self.assertGreater(doc["reachability"]["failed_polls"], 2)
        self.assertEqual(1, doc["reachability"]["drops"])
        self.assertEqual(rec["down_s"], doc["reachability"]["longest_down_s"])
        self.assertEqual(["8"], [m["id"] for m in doc["messages"]], "a message that arrived while it was down is found afterwards")
        self.assertIn("DROP at", text)
        self.assertIn("RECOVERED at", text)

    def test_wireless_reconnects_are_logged(self):
        clock = Clock()
        w = World(clock, serial=NAME + MDNS_SUFFIX, reachable={NAME + MDNS_SUFFIX}, advertised={NAME: ADDR})
        w.add(1, "baseline", minute_ago=5)
        state = {"polls": 0}
        orig_sleep = clock.sleep

        def sleep(s):
            orig_sleep(s)
            state["polls"] += 1
            if state["polls"] == 2:
                w.fake.reachable.clear()                       # the phone woke up on a new port: name unroutable
                w.add(9, "Your ACME code is %s." % CODE)

        code, text, doc, _ = run(w, minutes=1, interval=5, serial=NAME, sleep=sleep)
        self.assertEqual(0, code)
        self.assertEqual("wifi", doc["device"]["transport"])
        kinds = [e["event"] for e in doc["events"]]
        self.assertEqual(["reconnect_attempt", "reconnected"], kinds)
        self.assertEqual(ADDR, doc["events"][1]["address"])
        self.assertEqual(1, doc["reachability"]["reconnects_ok"])
        self.assertEqual(0, doc["reachability"]["failed_polls"], "the reconnect happened inside the fetch")
        self.assertEqual(["9"], [m["id"] for m in doc["messages"]])

    def test_phone_asleep_for_the_whole_run_after_check_is_never_reachable(self):
        clock = Clock()
        w = World(clock, serial=NAME + MDNS_SUFFIX, reachable={NAME + MDNS_SUFFIX}, advertised={NAME: ADDR}, connect_ok=False)

        def on_call(world):
            if "length(body)" in world.log[-1][-1]:             # the first real read: the phone has gone to sleep
                world.fake.reachable.clear()

        w.on_call = on_call
        code, text, doc, _ = run(w, minutes=1, interval=10, serial=NAME)
        self.assertEqual(3, code)
        self.assertEqual("never reachable", doc["status"])
        self.assertGreaterEqual(doc["reachability"]["failed_polls"], 1)
        self.assertEqual(1, doc["reachability"]["drops"])
        self.assertIn("unreachable", doc["events"][0]["detail"] if doc["events"][0]["event"] == "drop" else "unreachable")

    def test_a_malformed_reply_is_a_drop_not_a_crash(self):
        clock = Clock()
        w = World(clock)
        w.add(1, "baseline", minute_ago=5)
        state = {"polls": 0}
        orig_sleep = clock.sleep

        def sleep(s):
            orig_sleep(s)
            state["polls"] += 1
            if state["polls"] == 1:
                w.fake.stdout_override["content query --uri content://sms/inbox --projection "] = "garbage 482915\n"
            if state["polls"] == 2:
                w.fake.stdout_override.clear()

        code, text, doc, _ = run(w, minutes=1, interval=5, sleep=sleep)
        self.assertEqual(0, code)
        self.assertEqual(1, doc["reachability"]["drops"])
        self.assertNotIn("482915", text + json.dumps(doc))

    def test_permission_refused_mid_run_stops(self):
        clock = Clock()
        w = World(clock)
        w.add(1, "baseline", minute_ago=5)
        state = {"polls": 0}
        orig_sleep = clock.sleep

        def sleep(s):
            orig_sleep(s)
            state["polls"] += 1
            if state["polls"] == 1:
                w.fake.deny = True

        code, text, doc, _ = run(w, minutes=1, interval=5, sleep=sleep)
        self.assertEqual(4, code)
        self.assertIn("refused", text)


class OwnerSteps(unittest.TestCase):
    def test_no_device_is_waiting_and_reads_nothing(self):
        w = World(Clock(), state=None)
        code, text, doc, _ = run(w)
        self.assertEqual(3, code)
        self.assertEqual("waiting for owner step", doc["status"])
        self.assertIn("nothing was read", text)
        self.assertFalse([c for c in w.remote_commands() if c.startswith("content") and "_id<0" not in c])

    def test_unauthorized_stops_and_says_what_to_do(self):
        w = World(Clock(), state="unauthorized")
        code, text, doc, _ = run(w)
        self.assertEqual(3, code)
        self.assertIn("debugging prompt", text)
        self.assertFalse([c for c in w.remote_commands() if c.startswith("content") and "_id<0" not in c])

    def test_wireless_not_advertised_is_waiting_for_the_owner(self):
        w = World(Clock(), serial=NAME + MDNS_SUFFIX, reachable=set(), advertised={})
        code, text, doc, _ = run(w, serial=NAME)
        self.assertEqual(3, code)
        self.assertIn("not advertised", text)

    def test_permission_refused_is_exit_4(self):
        w = World(Clock(), deny=True)
        code, text, doc, _ = run(w)
        self.assertEqual(4, code)
        self.assertEqual("failed", doc["status"])


class ParseMeta(unittest.TestCase):
    def test_rows(self):
        rows = otp_delay.parse_meta("Row: 0 _id=12, date=1793836800000, date_sent=1793836799000\n"
                                    "Row: 1 _id=11, date=5, date_sent=0\n")
        self.assertEqual("12", rows[0]["id"])
        self.assertIsNone(rows[1]["date_sent"])
        self.assertEqual([], otp_delay.parse_meta("No result found.\n"))

    def test_malformed_or_forged_rows_are_refused_without_echo(self):
        for bad in ("garbage", "Row: 1 _id=1, date=1, date_sent=1", "Row: 0 _id=1, date=x, date_sent=1"):
            with self.assertRaises(Exception) as cm:
                otp_delay.parse_meta(bad)
            self.assertNotIn("garbage", str(cm.exception))

    def test_cli_rejects_silly_arguments(self):
        with self.assertRaises(SystemExit):
            otp_delay.main(["--minutes", "0"])
        with self.assertRaises(SystemExit):
            otp_delay.main(["--interval", "0.1"])


if __name__ == "__main__":
    unittest.main()
