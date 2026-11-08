import io
import json
import unittest

from line.sms import probe
from line.sms import AdbSource, SmsMessage

from .helpers import FakeAdb, MIN, T0, adb_rows, fixture_messages


def run(argv, fake, clock=lambda: (T0 + 40 * MIN) / 1000.0, sleep=lambda s: None, **kw):
    out = io.StringIO()
    src = AdbSource(adb_path="adb", runner=fake, **kw)
    code = probe.main(argv, source=src, out=out, clock=clock, sleep=sleep)
    lines = [json.loads(l) for l in out.getvalue().splitlines()]
    return code, lines, out.getvalue()


class ProbeTests(unittest.TestCase):
    def test_no_device_is_waiting_for_owner_step(self):
        code, lines, raw = run(["check"], FakeAdb([], state=None))
        self.assertEqual(3, code)
        self.assertEqual("waiting for owner step", lines[0]["status"])
        self.assertTrue(lines[0]["owner_steps"])

    def test_unauthorized_is_waiting_for_owner_step(self):
        code, lines, _ = run(["check"], FakeAdb([], state="unauthorized"))
        self.assertEqual(3, code)
        self.assertIn("USB debugging", lines[0]["detail"])

    def test_inbox_without_device_is_waiting(self):
        code, lines, _ = run(["inbox"], FakeAdb([], state=None))
        self.assertEqual(3, code)

    def test_check_ok_reports_android17_flag(self):
        code, lines, _ = run(["check"], FakeAdb(adb_rows(fixture_messages())))
        self.assertEqual(0, code)
        self.assertEqual("ok", lines[0]["status"])
        self.assertEqual("37", lines[0]["device"]["sdk"])
        self.assertTrue(lines[0]["android17_otp_protection_expected"])

    def test_check_flag_off_on_android16(self):
        props = {"ro.product.model": "Pixel", "ro.build.version.release": "16", "ro.build.version.sdk": "36",
                 "ro.build.version.security_patch": "2026-01-05", "ro.build.version.release_or_codename": "16"}
        code, lines, _ = run(["check"], FakeAdb([], props=props))
        self.assertFalse(lines[0]["android17_otp_protection_expected"])

    def test_permission_refused_exit_4(self):
        code, lines, _ = run(["check"], FakeAdb([], deny=True))
        self.assertEqual(4, code)
        code, lines, _ = run(["inbox"], FakeAdb([], deny=True))
        self.assertEqual(4, code)
        self.assertEqual("failed", lines[0]["status"])

    def test_inbox_output_is_masked(self):
        code, lines, raw = run(["inbox", "--since-minutes", "60"], FakeAdb(adb_rows(fixture_messages("adb"))))
        self.assertEqual(0, code)
        self.assertEqual(8, lines[0]["count"])
        msgs = [l for l in lines if l["probe"] == "message"]
        self.assertEqual(8, len(msgs))
        for planted in ("482915", "739104", "650318", "905512", "111222", "333444", "275306", "77881234"):
            self.assertNotIn(planted, raw)
        for word in ("ACME", "verification", "lunch", "Google", "EXAMPLEBANK", "5550100123"):
            self.assertNotIn(word, raw)
        first = [m for m in msgs if m["id"] == "101"][0]
        self.assertEqual("A***ME", first["from"])
        self.assertEqual({"found": True, "codes": 1, "ambiguous": False, "kinds": ["digits"]}, first["otp"])
        lunch = [m for m in msgs if m["id"] == "102"][0]
        self.assertFalse(lunch["otp"]["found"])
        amb = [m for m in msgs if m["id"] == "105"][0]
        self.assertTrue(amb["otp"]["ambiguous"])

    def test_inbox_respects_window_and_limit(self):
        fake = FakeAdb(adb_rows(fixture_messages("adb")))
        clock = lambda: (T0 + 31 * MIN) / 1000.0
        _, lines, _ = run(["inbox", "--since-minutes", "2", "--limit", "5"], fake, clock=clock)
        self.assertEqual(["108", "107"], [m["id"] for m in lines if m["probe"] == "message"])   # since = minute 29
        _, lines, _ = run(["inbox", "--since-minutes", "2", "--limit", "1"], fake, clock=clock)
        self.assertEqual(["108"], [m["id"] for m in lines if m["probe"] == "message"])

    def test_shape_is_short_and_shape_only(self):
        _, lines, _ = run(["inbox"], FakeAdb(adb_rows(fixture_messages("adb"))))
        for m in (l for l in lines if l["probe"] == "message"):
            self.assertLessEqual(len(m["shape"]), probe.SHAPE_CHARS)
            self.assertRegex(m["shape"], r"^[aA9 .,:;!?\-_()\[\]<>#$%&*+/=@'\"…]*$")

    def test_watch_measures_visibility_lag(self):
        """A plain message shows up fast; an OTP-shaped one only after the provider releases it."""
        clock_state = {"t": (T0 + 100 * MIN) / 1000.0}
        plain = {"_id": 201, "address": "+15550100123", "date": T0 + 100 * MIN + 5_000, "type": 1, "body": "see you soon"}
        otp = {"_id": 202, "address": "ACME", "date": T0 + 100 * MIN + 6_000, "type": 1, "body": "Your code is 482915"}
        fake = FakeAdb([])

        def sleep(s):
            clock_state["t"] += s
            visible = []
            if clock_state["t"] >= (T0 + 100 * MIN) / 1000.0 + 10:
                visible.append(plain)                       # visible ~10s after arrival
            if clock_state["t"] >= (T0 + 100 * MIN) / 1000.0 + 600:
                visible.append(otp)                         # withheld for ~10 minutes
            fake.rows = sorted(visible, key=lambda r: -r["date"])

        code, lines, raw = run(["watch", "--duration", "700", "--interval", "30"], fake,
                               clock=lambda: clock_state["t"], sleep=sleep)
        self.assertEqual(0, code)
        news = [l for l in lines if l["probe"] == "new_message"]
        self.assertEqual({"201", "202"}, {n["id"] for n in news})
        done = lines[-1]
        self.assertEqual("done", done["status"])
        self.assertLess(done["plain_lag_s"][0], 60)
        self.assertGreater(done["otp_shaped_lag_s"][0], 500)
        self.assertNotIn("482915", raw)
        self.assertNotIn("see you soon", raw)

    def test_describe_is_the_only_message_to_output_path(self):
        m = SmsMessage("1", "+15550100123", T0, "Your code is 482915")
        rec = probe.describe(m)
        self.assertNotIn("482915", json.dumps(rec))
        self.assertNotIn("5550100123", json.dumps(rec))


if __name__ == "__main__":
    unittest.main()
