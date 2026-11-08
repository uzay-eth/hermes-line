"""Sweep: push every fixture body through every output path and look for the planted code.

The library must not leak even with the logging MaskingFilter switched OFF (the filter is
defence in depth, not the mechanism), and `.reveal()` may only appear at known call sites.
"""
import io
import json
import logging
import os
import re
import unittest

from line.sms import probe
from line.sms import ApprovedTask, AdbSource, Decision, FixtureSource, SmsMessage, OtpMatcher, wait_for_code
from line.sms.sources.base import ParseError

from .helpers import FakeAdb, MIN, T0, load_otp_cases, adb_rows

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "plugin", "line", "sms")


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(self.format(record))


class LeakSweep(unittest.TestCase):
    def setUp(self):
        self.cap = Capture()
        self.lg = logging.getLogger("line.sms")
        self.lg.addHandler(self.cap)
        self.lg.setLevel(logging.DEBUG)
        self.addCleanup(self.lg.removeHandler, self.cap)

    def test_no_planted_code_appears_anywhere(self):
        cases = [c for c in load_otp_cases() if c["codes"]]
        self.assertGreater(len(cases), 30)
        for case in cases:
            body = case["body"]
            with self.subTest(case["name"]):
                self.cap.lines.clear()
                m = SmsMessage("42", "ACME", T0 + MIN, body, source="fixture")
                surfaces = [repr(m), str(m), "%s" % (m,), f"{m}", m.log_summary(), repr(m.body), str(m.body)]
                surfaces.append(json.dumps(probe.describe(m)))

                task = ApprovedTask("t", ("ACME",), T0, T0 + 5 * MIN)
                out = OtpMatcher(task).evaluate([m], now_ms=T0 + 2 * MIN)
                surfaces += [repr(out), str(out), out.log_line(), repr(out.message), repr(out.code)]

                src = FixtureSource([m], clock=lambda: (T0 + 2 * MIN) / 1000.0)
                clock = {"t": (T0 + 2 * MIN) / 1000.0}
                wait_for_code(src, OtpMatcher(task), timeout_s=30, interval_s=1,
                              clock=lambda: clock["t"], sleep=lambda s: clock.__setitem__("t", clock["t"] + s))

                # probe end-to-end through the adb parser
                buf = io.StringIO()
                fake = FakeAdb(adb_rows([m]))
                probe.main(["inbox"], source=AdbSource(adb_path="adb", runner=fake), out=buf,
                           clock=lambda: (T0 + 2 * MIN) / 1000.0)
                surfaces.append(buf.getvalue())

                # an adb parse failure carrying this body must not echo it
                try:
                    AdbSource(adb_path="adb", runner=lambda a, t: __import__("line.sms", fromlist=["x"]).sources.adb.RunResult(
                        0, "garbage " + body + "\n", "")).fetch()
                except ParseError as exc:
                    surfaces.append(str(exc))
                    surfaces.append(repr(exc))
                except Exception as exc:  # DeviceUnavailable for the fake `devices` call is fine
                    surfaces.append(str(exc))

                surfaces += self.cap.lines
                blob = "\n".join(surfaces)
                for code in case["codes"]:
                    self.assertNotIn(code, blob)
                # the raw body text (for bodies with 8+ chars of prose) must not appear either
                if len(body) >= 12:
                    self.assertNotIn(body, blob)
                    self.assertNotIn(body.split()[0] + " " + body.split()[1] if len(body.split()) > 1 else body, blob)


class RevealAudit(unittest.TestCase):
    """`.reveal()` is the audit surface. New call sites need a conscious update here."""

    ALLOWED = {
        "secret.py": 2,       # the definition + its docstring mention
        "otp.py": 1,          # extract_otp reads the body to analyse it
        "matching.py": 1,     # MatchOutcome.release(): the single approved read
        "scrub.py": 1,        # ValueScrubber.register(): builds the exact-value pattern, keeps no plain copy
        "delivery.py": 1,     # BrowserTypeSink.fill: the code goes to the page; nothing else unwraps it
        "probe.py": 1,        # describe(): feeds redact_all (shape only)
    }

    def test_reveal_call_sites(self):
        found = {}
        for dirpath, _, files in os.walk(ROOT):
            if "__pycache__" in dirpath:
                continue
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(dirpath, f)
                with open(path, encoding="utf-8") as fh:
                    n = len(re.findall(r"\.reveal\(\)|def reveal\(", fh.read()))
                if n:
                    found[os.path.relpath(path, ROOT)] = n
        self.assertEqual(self.ALLOWED, found)

    def test_sink_is_the_only_reveal_in_delivery(self):
        with open(os.path.join(ROOT, "delivery.py"), encoding="utf-8") as fh:
            lines = [l for l in fh if ".reveal()" in l]
        self.assertEqual(1, len(lines))
        self.assertIn('"text": secret.reveal()', lines[0])

    def test_no_cleartext_tool_in_the_plugin(self):
        for dirpath, _, files in os.walk(ROOT):
            for f in files:
                if f.endswith(".py"):
                    with open(os.path.join(dirpath, f), encoding="utf-8") as fh:
                        text = fh.read()
                    for needle in ("line_otp_read", "DeliveryMode", "def cleartext"):
                        self.assertNotIn(needle, text, f)

    def test_release_is_the_only_code_reveal(self):
        with open(os.path.join(ROOT, "matching.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertEqual(1, src.count(".reveal()"))
        self.assertIn("return self.code.reveal()", src)

    def test_no_print_or_stdout_in_library(self):
        for dirpath, _, files in os.walk(ROOT):
            for f in files:
                if f.endswith(".py") and f != "probe.py":   # probe.py is the CLI: its output is masked and tested
                    with open(os.path.join(dirpath, f), encoding="utf-8") as fh:
                        text = fh.read()
                    self.assertNotRegex(text, r"(?m)^\s*print\(", f)
                    self.assertNotIn("sys.stdout", text, f)

    def test_no_file_writes_in_library(self):
        for dirpath, _, files in os.walk(ROOT):
            for f in files:
                if f.endswith(".py"):
                    with open(os.path.join(dirpath, f), encoding="utf-8") as fh:
                        text = fh.read()
                    self.assertNotRegex(text, r"open\([^)]*['\"][wa]b?['\"]", f)


if __name__ == "__main__":
    unittest.main()
