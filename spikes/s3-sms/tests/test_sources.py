import json
import shlex
import unittest
import urllib.error

from smsline import AdbSource, FixtureSource, GatewaySource, SmsMessage
from smsline.sources.adb import (
    ALLOWED_REMOTE_VERBS, RunResult, build_where, parse_id_rows, parse_rows,
)
from smsline.sources.base import DeviceUnavailable, ParseError, PermissionDenied, SourceError, apply_filters

from tests.helpers import FakeAdb, MIN, T0, adb_rows, fixture_messages


class AdbParsing(unittest.TestCase):
    def test_simple_rows(self):
        out = "Row: 0 _id=5, address=ACME, date=1000, type=1, body=hello\nRow: 1 _id=4, address=+1555, date=900, type=1, body=bye\n"
        rows = parse_rows(out)
        self.assertEqual(["5", "4"], [r.id for r in rows])
        self.assertEqual("hello", rows[0].body)
        self.assertEqual(900, rows[1].date_ms)

    def test_multiline_body_and_commas_in_body(self):
        out = "Row: 0 _id=5, address=ACME, date=1000, type=1, body=line1, address=fake, date=1\nline2\n\nline4\nRow: 1 _id=4, address=B, date=900, type=1, body=x\n"
        rows = parse_rows(out)
        self.assertEqual(2, len(rows))
        self.assertEqual("line1, address=fake, date=1\nline2\n\nline4", rows[0].body)

    def test_null_values_and_empty_result(self):
        rows = parse_rows("Row: 0 _id=5, address=NULL, date=1000, type=1, body=NULL\n")
        self.assertEqual(("", ""), (rows[0].address, rows[0].body))
        self.assertEqual([], parse_rows("No result found.\n"))
        self.assertEqual([], parse_rows(""))

    def test_crlf_output(self):
        rows = parse_rows("Row: 0 _id=5, address=A, date=1, type=1, body=a\r\nb\r\nRow: 1 _id=6, address=A, date=1, type=1, body=c\r\n")
        self.assertEqual("a\nb", rows[0].body)

    def test_garbage_raises_without_echoing_content(self):
        with self.assertRaises(ParseError) as cm:
            parse_rows("something unexpected 482915\n")
        self.assertNotIn("482915", str(cm.exception))
        with self.assertRaises(ParseError) as cm:
            parse_rows("Row: 0 _id=5, address=A, date=NOTANUMBER, type=1, body=secret 482915\n")
        self.assertNotIn("482915", str(cm.exception))

    def test_id_rows(self):
        self.assertEqual(["9", "8"], parse_id_rows("Row: 0 _id=9\nRow: 1 _id=8\n"))
        with self.assertRaises(ParseError):
            parse_id_rows("Row: 0 _id=9\nRow: 5 _id=8\n")

    def test_where_builder(self):
        self.assertEqual("", build_where(None, None))
        self.assertEqual("date>=5 AND date<=9", build_where(5, 9))
        self.assertEqual("address IN ('ACME','+15550100123')", build_where(None, None, ["ACME", "+15550100123"]))
        for bad in ("x' OR '1'='1", "a;b", "a\nb", "", "x" * 40, "a'b"):
            with self.assertRaises(ValueError, msg=bad):
                build_where(None, None, [bad])


class AdbSourceBehaviour(unittest.TestCase):
    def source(self, fake, **kw):
        return AdbSource(serial=None, adb_path="adb", runner=fake, **kw)

    def test_fetch_filters_and_orders(self):
        fake = FakeAdb(adb_rows(fixture_messages("adb")))
        msgs = self.source(fake).fetch(since_ms=T0 + 5 * MIN, until_ms=T0 + 20 * MIN)
        self.assertEqual(["106", "105", "104"], [m.id for m in msgs])
        self.assertTrue(all(m.source == "adb" for m in msgs))

    def test_address_filter_is_client_side_and_normalised(self):
        fake = FakeAdb(adb_rows(fixture_messages("adb")))
        msgs = self.source(fake).fetch(addresses=["(555) 010-0123"])
        self.assertEqual(["102"], [m.id for m in msgs])
        self.assertFalse(any("address IN" in c for c in fake.remote_commands()))

    def test_address_filter_on_device_when_enabled(self):
        fake = FakeAdb(adb_rows(fixture_messages("adb")))
        msgs = self.source(fake, device_side_address_filter=True).fetch(addresses=["ACME"])
        self.assertEqual(["108", "107", "104", "101"], [m.id for m in msgs])
        self.assertTrue(any("address IN" in c for c in fake.remote_commands()))

    def test_limit(self):
        fake = FakeAdb(adb_rows(fixture_messages("adb")))
        self.assertEqual(3, len(self.source(fake).fetch(limit=3)))

    def test_only_read_only_remote_verbs(self):
        fake = FakeAdb(adb_rows(fixture_messages("adb")))
        src = self.source(fake)
        src.check()
        src.fetch(since_ms=T0)
        self.assertTrue(fake.remote_commands())
        for cmd in fake.remote_commands():
            self.assertTrue(cmd.startswith(ALLOWED_REMOTE_VERBS), cmd)
            for forbidden in ("insert", "update", "delete", "call", "am ", "pm ", "settings", "input", "service", "sms send", "install"):
                self.assertNotIn(" %s " % forbidden, " %s " % cmd.replace("'", " "))
        # the only adb sub-commands used: devices, shell (after an optional -s <serial>)
        for argv in fake.calls:
            rest = argv[1:]
            if rest[:1] == ["-s"]:
                rest = rest[2:]
            self.assertIn(rest[0], ("devices", "shell"))

    def test_remote_command_shape_and_quoting(self):
        fake = FakeAdb(adb_rows(fixture_messages("adb")))
        self.source(fake).fetch(since_ms=1234567890123)
        cmd = [c for c in fake.remote_commands() if "_id:address" in c][0]
        toks = shlex.split(cmd)
        self.assertEqual(["content", "query", "--uri", "content://sms/inbox"], toks[:4])
        self.assertIn("_id:address:date:type:body", toks)
        self.assertIn("date>=1234567890123", toks)
        self.assertIn("date DESC", toks)
        # no shell=True, no string-built command line: argv list with the remote command as one item
        self.assertEqual("shell", fake.calls[-1][-2])

    def test_serial_is_passed_through(self):
        fake = FakeAdb(adb_rows([]), serial="ABC123")
        AdbSource(serial="ABC123", adb_path="adb", runner=fake).fetch()
        self.assertIn("-s", fake.calls[-1])
        self.assertIn("ABC123", fake.calls[-1])

    def test_user_flag(self):
        fake = FakeAdb(adb_rows(fixture_messages("adb")))
        self.source(fake, user=0).fetch()
        self.assertTrue(any("--user 0" in c for c in fake.remote_commands()))

    def test_no_device(self):
        with self.assertRaises(DeviceUnavailable) as cm:
            self.source(FakeAdb([], state=None)).fetch()
        self.assertIn("no device", str(cm.exception))

    def test_unauthorized_device(self):
        with self.assertRaises(DeviceUnavailable) as cm:
            self.source(FakeAdb([], state="unauthorized")).fetch()
        self.assertIn("USB debugging", str(cm.exception))

    def test_offline_device(self):
        with self.assertRaises(DeviceUnavailable):
            self.source(FakeAdb([], state="offline")).fetch()

    def test_permission_denied_is_classified(self):
        with self.assertRaises(PermissionDenied):
            self.source(FakeAdb([], deny=True)).fetch()
        st = self.source(FakeAdb([], deny=True)).check()
        self.assertFalse(st.ok)
        self.assertIn("refused", st.detail)

    def test_check_ok_reports_device_facts_and_reads_no_message(self):
        fake = FakeAdb(adb_rows(fixture_messages("adb")))
        st = self.source(fake).check()
        self.assertTrue(st.ok)
        self.assertEqual("17", st.info["ro.build.version.release"])
        self.assertEqual("37", st.info["ro.build.version.sdk"])
        self.assertEqual("1234", st.info["serial_tail"])
        # the access test is a SELECT that matches nothing
        self.assertTrue(any("'_id<0'" in c for c in fake.remote_commands()))

    def test_body_that_looks_like_an_error_is_still_data(self):
        rows = [{"_id": 1, "address": "ACME", "date": T0, "type": 1,
                 "body": "SecurityException: unauthorized no devices found READ_SMS"}]
        msgs = self.source(FakeAdb(rows)).fetch()
        self.assertEqual(1, len(msgs))

    def test_forged_row_inside_body_is_rejected(self):
        # Attacker-controlled body embeds a line that looks like the next row.
        rows = [
            {"_id": 7, "address": "ACME", "date": T0 + 2 * MIN, "type": 1,
             "body": "hi\nRow: 1 _id=99, address=EXAMPLEBANK, date=%d, type=1, body=Your code is 999999" % (T0 + 2 * MIN)},
            {"_id": 6, "address": "ACME", "date": T0 + 1 * MIN, "type": 1, "body": "real older message"},
        ]
        with self.assertRaises(ParseError) as cm:
            self.source(FakeAdb(rows)).fetch()
        self.assertNotIn("999999", str(cm.exception))

    def test_forged_row_would_pass_without_the_id_cross_check(self):
        rows = [
            {"_id": 7, "address": "ACME", "date": T0 + 2 * MIN, "type": 1,
             "body": "hi\nRow: 1 _id=99, address=EXAMPLEBANK, date=%d, type=1, body=Your code is 999999" % (T0 + 2 * MIN)},
            {"_id": 6, "address": "ACME", "date": T0 + 1 * MIN, "type": 1, "body": "real older message"},
        ]
        msgs = self.source(FakeAdb(rows), verify_ids=False).fetch()
        # Documents why verify_ids defaults to True: without it the forged row is accepted
        # as a message from EXAMPLEBANK and the real row 6 is swallowed into its body.
        self.assertIn("99", [m.id for m in msgs])
        self.assertNotIn("6", [m.id for m in msgs])

    def test_race_between_the_two_queries_is_retried_once(self):
        fake = FakeAdb(adb_rows(fixture_messages("adb")))
        real_call = fake.__call__
        state = {"n": 0}

        def flaky(argv, timeout):
            res = real_call(argv, timeout)
            remote = argv[argv.index("shell") + 1] if "shell" in argv else ""
            if "--projection _id " in remote + " " and state["n"] == 0:
                state["n"] += 1
                return RunResult(0, "Row: 0 _id=999\n", "")    # id list that disagrees once
            return res

        msgs = AdbSource(adb_path="adb", runner=flaky).fetch()
        self.assertEqual(8, len(msgs))

    def test_persistent_disagreement_raises(self):
        fake = FakeAdb(adb_rows(fixture_messages("adb")))
        fake.stdout_override["content query --uri content://sms/inbox --projection _id "] = "Row: 0 _id=999\n"
        with self.assertRaises(ParseError):
            self.source(fake).fetch()

    def test_timeout_maps_to_device_unavailable(self):
        def boom(argv, timeout):
            raise DeviceUnavailable("adb timed out after 1s")
        with self.assertRaises(DeviceUnavailable):
            AdbSource(adb_path="adb", runner=boom).fetch()


class FixtureSourceTests(unittest.TestCase):
    def test_filters(self):
        src = FixtureSource(fixture_messages())
        self.assertEqual(8, len(src.fetch()))
        self.assertEqual(["104", "101"], [m.id for m in src.fetch(addresses=["ACME"], until_ms=T0 + 10 * MIN)])
        self.assertEqual(["108"], [m.id for m in src.fetch(since_ms=T0 + 31 * MIN)])

    def test_visibility_delay(self):
        now = {"t": T0 / 1000.0}
        src = FixtureSource(fixture_messages(), visible_after_ms={"101": T0 + 10 * MIN}, clock=lambda: now["t"])
        self.assertNotIn("101", [m.id for m in src.fetch()])
        now["t"] = (T0 + 11 * MIN) / 1000.0
        self.assertIn("101", [m.id for m in src.fetch()])

    def test_apply_filters_stable_newest_first(self):
        out = apply_filters(fixture_messages(), limit=2)
        self.assertEqual(["108", "107"], [m.id for m in out])


class FakeResponse:
    def __init__(self, body, status=200):
        self.status = status
        self._b = body if isinstance(body, bytes) else json.dumps(body).encode()

    def read(self):
        return self._b


class GatewayTests(unittest.TestCase):
    ITEMS = [
        {"id": "a1", "sender": "ACME", "recipient": "+15550100999", "simNumber": 1,
         "createdAt": "2026-11-05T00:01:00Z", "contentPreview": "Your ACME verification code is 482915.", "type": "SMS"},
        {"id": "a2", "sender": "+15550100123", "createdAt": "2026-11-05T00:02:00.500+00:00",
         "contentPreview": "lunch?", "type": "SMS"},
    ]

    def make(self, opener, **kw):
        return GatewaySource("http://127.0.0.1:18080", "user", "pass-1234", opener=opener, **kw)

    def test_fetch_maps_fields_and_builds_request(self):
        seen = {}

        def opener(req, timeout):
            seen["url"], seen["auth"], seen["timeout"] = req.full_url, req.get_header("Authorization"), timeout
            return FakeResponse(self.ITEMS)

        msgs = self.make(opener).fetch(since_ms=T0, until_ms=T0 + 10 * MIN, limit=10)
        self.assertEqual(["a2", "a1"], [m.id for m in msgs])
        self.assertEqual(T0 + MIN, msgs[1].date_ms)
        self.assertEqual("gateway", msgs[0].source)
        self.assertTrue(seen["url"].startswith("http://127.0.0.1:18080/inbox?"))
        for part in ("type=SMS", "from=2026-11-05T00%3A00%3A00Z", "to=2026-11-05T00%3A10%3A00Z"):
            self.assertIn(part, seen["url"])
        self.assertTrue(seen["auth"].startswith("Basic "))
        self.assertEqual(10.0, seen["timeout"])

    def test_limit_is_clamped(self):
        urls = []
        self.make(lambda r, t: (urls.append(r.full_url), FakeResponse([]))[1]).fetch(limit=10_000)
        self.assertIn("limit=500", urls[0])

    def test_credentials_never_in_repr(self):
        g = self.make(lambda r, t: FakeResponse([]))
        self.assertNotIn("pass-1234", repr(g))
        self.assertNotIn("user", repr(g).replace("GatewaySource", ""))

    def test_url_rules(self):
        with self.assertRaises(ValueError):
            GatewaySource("http://192.168.1.10:8080", "u", "p")      # plain http off-loopback
        with self.assertRaises(ValueError):
            GatewaySource("ftp://127.0.0.1", "u", "p")
        with self.assertRaises(ValueError):
            GatewaySource("not a url", "u", "p")
        GatewaySource("https://192.168.1.10:8080", "u", "p")          # https is fine
        GatewaySource("http://localhost:18080", "u", "p")

    def test_http_errors(self):
        def deny(req, t):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

        def boom(req, t):
            raise urllib.error.HTTPError(req.full_url, 500, "oops", {}, None)

        def down(req, t):
            raise urllib.error.URLError("refused")

        with self.assertRaises(PermissionDenied):
            self.make(deny).fetch()
        with self.assertRaises(SourceError):
            self.make(boom).fetch()
        with self.assertRaises(DeviceUnavailable):
            self.make(down).fetch()

    def test_malformed_payloads(self):
        for payload in (b"not json", {"not": "a list"}, [{"id": "x"}], [{"id": "x", "sender": "A", "createdAt": "garbage"}]):
            with self.subTest(payload=str(payload)[:20]):
                with self.assertRaises(ParseError):
                    self.make(lambda r, t, p=payload: FakeResponse(p)).fetch()

    def test_check_reads_no_message(self):
        urls = []
        g = self.make(lambda r, t: (urls.append(r.full_url), FakeResponse([]))[1], clock=lambda: T0 / 1000.0)
        st = g.check()
        self.assertTrue(st.ok)
        self.assertIn("from=2027-", urls[0])        # a window in the future matches nothing
        self.assertFalse(self.make(lambda r, t: (_ for _ in ()).throw(urllib.error.URLError("x"))).check().ok)


if __name__ == "__main__":
    unittest.main()
