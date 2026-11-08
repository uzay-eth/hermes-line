import json
import shlex
import unittest
import urllib.error

from line.sms import AdbSource, FixtureSource, GatewaySource, SmsMessage
from line.sms.sources.adb import (
    ALLOWED_REMOTE_VERBS, MDNS_SUFFIX, MSG_NOT_ADVERTISED, MSG_UNREACHABLE, PROJECTION, RunResult, build_where,
    classify_serial, parse_mdns_services, parse_rows,
)
from line.sms.sources.base import DeviceUnavailable, ParseError, PermissionDenied, SourceError, apply_filters

from .helpers import FakeAdb, MIN, T0, adb_rows, fixture_messages


def row(n, id_, address, date, type_, body):
    """One output row exactly as the provider prints PROJECTION."""
    la = "NULL" if address is None else str(len(address))
    lb = "NULL" if body is None else str(len(body))
    return "Row: %d _id=%s, date=%s, type=%s, length(address)=%s, length(body)=%s, address=%s, body=%s" % (
        n, id_, date, type_, la, lb, "NULL" if address is None else address, "NULL" if body is None else body)


class AdbParsing(unittest.TestCase):
    def test_simple_rows(self):
        out = row(0, 5, "ACME", 1000, 1, "hello") + "\n" + row(1, 4, "+1555", 900, 1, "bye") + "\n"
        rows = parse_rows(out)
        self.assertEqual(["5", "4"], [r.id for r in rows])
        self.assertEqual("hello", rows[0].body)
        self.assertEqual(900, rows[1].date_ms)

    def test_multiline_body_and_commas_in_body(self):
        body = "line1, address=fake, date=1\nline2\n\nline4"
        out = row(0, 5, "ACME", 1000, 1, body) + "\n" + row(1, 4, "B", 900, 1, "x") + "\n"
        rows = parse_rows(out)
        self.assertEqual(2, len(rows))
        self.assertEqual(body, rows[0].body)

    def test_forged_row_inside_body_is_just_data(self):
        """Length-prefixed rows: a body that looks like further rows cannot create or swallow a message."""
        body = "hi\n" + row(1, 99, "EXAMPLEBANK", 5, 1, "Your code is 999999")
        out = row(0, 7, "ACME", 1000, 1, body) + "\n" + row(1, 6, "ACME", 900, 1, "real older message") + "\n"
        rows = parse_rows(out)
        self.assertEqual(["7", "6"], [r.id for r in rows])
        self.assertEqual(body, rows[0].body)
        self.assertEqual("ACME", rows[0].address)

    def test_forged_address_cannot_split_the_row(self):
        address = "ACME, date=1, type=1, length(address)=0, length(body)=3, address=, body=xyz"
        rows = parse_rows(row(0, 1, address, 1000, 1, "hello") + "\n")
        self.assertEqual(address, rows[0].address)
        self.assertEqual(1000, rows[0].date_ms)
        self.assertEqual("hello", rows[0].body)

    def test_output_that_disagrees_with_the_reported_lengths_is_refused(self):
        good = row(0, 5, "ACME", 1000, 1, "hello")
        for bad in (
            good + "Row: 1 _id=9, date=1, type=1, length(address)=1, length(body)=1, address=E, body=Z\n",   # text after the length
            good.replace("length(body)=5", "length(body)=9") + "\n",                                          # shorter than reported
            good.replace("length(body)=5", "length(body)=2") + "\n",                                          # longer than reported
            good + "\n\n",                                                                                    # stray blank line
            row(1, 5, "ACME", 1000, 1, "hello") + "\n",                                                        # row index must start at 0
            good + "Z" + row(1, 6, "ACME", 900, 1, "x") + "\n",                                                # junk where the newline must be
        ):
            with self.assertRaises(ParseError, msg=bad):
                parse_rows(bad)

    def test_null_values_and_empty_result(self):
        rows = parse_rows(row(0, 5, None, 1000, 1, None) + "\n")
        self.assertEqual(("", ""), (rows[0].address, rows[0].body))
        self.assertEqual("", parse_rows(row(0, 5, "", 1000, 1, "") + "\n")[0].body)
        self.assertEqual([], parse_rows("No result found.\n"))
        self.assertEqual([], parse_rows(""))

    def test_crlf_output_from_an_adb_that_translates_line_endings(self):
        out = (row(0, 5, "A", 1, 1, "a\nb") + "\n" + row(1, 6, "A", 1, 1, "c") + "\n").replace("\n", "\r\n")
        rows = parse_rows(out)
        self.assertEqual("a\nb", rows[0].body)
        self.assertEqual("c", rows[1].body)

    def test_a_body_with_real_crlf_is_kept_when_lengths_agree(self):
        rows = parse_rows(row(0, 5, "A", 1, 1, "a\r\nb") + "\n")
        self.assertEqual("a\r\nb", rows[0].body)

    def test_garbage_raises_without_echoing_content(self):
        with self.assertRaises(ParseError) as cm:
            parse_rows("something unexpected 482915\n")
        self.assertNotIn("482915", str(cm.exception))
        with self.assertRaises(ParseError) as cm:
            parse_rows("Row: 0 _id=5, date=NOTANUMBER, type=1, length(address)=1, length(body)=9, address=A, body=secret 482915\n")
        self.assertNotIn("482915", str(cm.exception))

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

    def test_one_adb_invocation_per_fetch(self):
        """Latency: a `content query` costs seconds of phone-side start-up, so a fetch is exactly one adb shell."""
        fake = FakeAdb(adb_rows(fixture_messages("adb")))
        src = self.source(fake)
        src.fetch(since_ms=T0)
        self.assertEqual(1, len(fake.calls))
        self.assertEqual("shell", fake.calls[0][-2])
        src.fetch(since_ms=T0)
        self.assertEqual(2, len(fake.calls), "no devices / getprop / id-verification calls per poll")

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
        # the only adb sub-commands used: shell and get-serialno (after an optional -s <serial>)
        for argv in fake.calls:
            rest = argv[1:]
            if rest[:1] == ["-s"]:
                rest = rest[2:]
            self.assertIn(rest[0], ("shell", "get-serialno"))

    def test_remote_command_shape_and_quoting(self):
        fake = FakeAdb(adb_rows(fixture_messages("adb")))
        self.source(fake).fetch(since_ms=1234567890123)
        cmd = [c for c in fake.remote_commands() if "length(body)" in c][0]
        toks = shlex.split(cmd)
        self.assertEqual(["content", "query", "--uri", "content://sms/inbox"], toks[:4])
        self.assertIn(":".join(PROJECTION), toks)
        self.assertIn("date>=1234567890123", toks)
        self.assertIn("date DESC", toks)
        # no shell=True, no string-built command line: argv list with the remote command as one item
        self.assertEqual("shell", fake.calls[-1][-2])

    def test_serial_is_passed_through(self):
        fake = FakeAdb(adb_rows([]), serial="ABC123")
        AdbSource(serial="ABC123", adb_path="adb", runner=fake).fetch()
        self.assertEqual(["adb", "-s", "ABC123"], fake.calls[-1][:3])

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
        with self.assertRaises(DeviceUnavailable) as cm:
            self.source(FakeAdb([], state="offline")).fetch()
        self.assertIn("offline", str(cm.exception))

    def test_a_configured_serial_that_is_not_attached_is_an_error_never_the_other_device(self):
        fake = FakeAdb(adb_rows(fixture_messages("adb")), serial="OTHERPHONE", reachable={"OTHERPHONE"})
        with self.assertRaises(DeviceUnavailable):
            AdbSource(serial="WANTED123", adb_path="adb", runner=fake).fetch()
        self.assertTrue(all(c[1:3] == ["-s", "WANTED123"] for c in fake.calls))

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
        self.assertEqual("usb", st.info["transport"])
        # the access test is a SELECT that matches nothing
        self.assertTrue(any("'_id<0'" in c for c in fake.remote_commands()))

    def test_body_that_looks_like_an_error_is_still_data(self):
        rows = [{"_id": 1, "address": "ACME", "date": T0, "type": 1,
                 "body": "SecurityException: unauthorized no devices found READ_SMS"}]
        msgs = self.source(FakeAdb(rows)).fetch()
        self.assertEqual(1, len(msgs))

    def test_forged_row_inside_body_creates_no_extra_message(self):
        forged = "hi\n" + row(1, 99, "EXAMPLEBANK", T0 + 2 * MIN, 1, "Your code is 999999")
        rows = [
            {"_id": 7, "address": "ACME", "date": T0 + 2 * MIN, "type": 1, "body": forged},
            {"_id": 6, "address": "ACME", "date": T0 + 1 * MIN, "type": 1, "body": "real older message"},
        ]
        msgs = self.source(FakeAdb(rows)).fetch()
        self.assertEqual(["7", "6"], [m.id for m in msgs])
        self.assertEqual({"ACME"}, {m.address for m in msgs})

    def test_tampered_output_is_refused(self):
        fake = FakeAdb(adb_rows(fixture_messages("adb")))
        fake.stdout_override["content query --uri content://sms/inbox --projection "] = (
            row(0, 7, "ACME", T0, 1, "hello").replace("length(body)=5", "length(body)=3") + "\n")
        with self.assertRaises(ParseError):
            self.source(fake).fetch()

    def test_timeout_maps_to_device_unavailable(self):
        def boom(argv, timeout):
            raise DeviceUnavailable("adb timed out after 1s")
        with self.assertRaises(DeviceUnavailable):
            AdbSource(adb_path="adb", runner=boom).fetch()


NAME = "adb-5B050DLCQ000P3-eQkDjp"
FULL = NAME + MDNS_SUFFIX
ADDR = "192.168.1.32:34897"


class WirelessTargets(unittest.TestCase):
    def test_serial_kinds(self):
        self.assertEqual(("mdns", FULL), classify_serial(NAME))
        self.assertEqual(("mdns", FULL), classify_serial(FULL))
        self.assertEqual(("hostport", ADDR), classify_serial(ADDR))
        self.assertEqual(("usb", "5B050DLCQ000P3"), classify_serial("5B050DLCQ000P3"))
        self.assertEqual(("none", None), classify_serial(""))
        self.assertEqual(("none", None), classify_serial(None))

    def test_mdns_list_parsing_ignores_junk(self):
        out = "List of discovered mdns services\n%s\t_adb-tls-connect._tcp\t%s\nbad line\nx\ty\tnot-an-address\n" % (NAME, ADDR)
        self.assertEqual([(NAME, "_adb-tls-connect._tcp", ADDR)], parse_mdns_services(out))

    def test_the_mdns_name_is_passed_with_the_service_suffix(self):
        fake = FakeAdb(adb_rows(fixture_messages("adb")), serial=FULL, reachable={FULL})
        AdbSource(serial=NAME, adb_path="adb", runner=fake).fetch()
        self.assertEqual(["adb", "-s", FULL], fake.calls[0][:3])


class WirelessRecovery(unittest.TestCase):
    def fake(self, **kw):
        kw.setdefault("reachable", set())                       # the configured name is not currently routable
        kw.setdefault("advertised", {NAME: ADDR})
        return FakeAdb(adb_rows(fixture_messages("adb")), serial=FULL, **kw)

    def src(self, fake, **kw):
        events = []
        s = AdbSource(serial=NAME, adb_path="adb", runner=fake, on_event=lambda k, i: events.append(k), **kw)
        return s, events

    def test_not_found_triggers_mdns_connect_and_one_retry(self):
        fake = self.fake()
        s, events = self.src(fake)
        msgs = s.fetch()
        self.assertEqual(8, len(msgs))
        verbs = [c[3] if c[1] == "-s" else c[1] for c in fake.calls]
        self.assertEqual(["shell", "mdns", "connect", "shell"], verbs)
        self.assertEqual(["adb", "-s", ADDR], fake.calls[-1][:3], "the retry goes to the freshly advertised address")
        self.assertEqual(["reconnect_attempt", "reconnected"], events)
        self.assertEqual({"reconnect_attempts": 1, "reconnects_ok": 1}, s.stats)

    def test_the_learned_route_is_used_until_it_fails(self):
        fake = self.fake()
        s, _ = self.src(fake)
        s.fetch()
        n = len(fake.calls)
        s.fetch()
        self.assertEqual(n + 1, len(fake.calls), "second fetch: one shell, no recovery")
        self.assertEqual(ADDR, fake.calls[-1][2])
        fake.reachable.discard(ADDR)                           # the port changed: the route is stale
        fake.advertised = {NAME: "192.168.1.32:40001"}
        self.assertEqual(8, len(s.fetch()))
        self.assertEqual("192.168.1.32:40001", fake.calls[-1][2])

    def test_not_advertised_is_a_specific_error(self):
        fake = self.fake(advertised={})
        s, _ = self.src(fake)
        with self.assertRaises(DeviceUnavailable) as cm:
            s.fetch()
        self.assertEqual(MSG_NOT_ADVERTISED, str(cm.exception))
        self.assertNotIn("connect", [c[3] if c[1] == "-s" else c[1] for c in fake.calls])

    def test_another_phone_advertised_is_not_adopted(self):
        fake = self.fake(advertised={"adb-OTHERPHONE-zzzzzz": "192.168.1.99:5555"})
        s, _ = self.src(fake)
        with self.assertRaises(DeviceUnavailable) as cm:
            s.fetch()
        self.assertEqual(MSG_NOT_ADVERTISED, str(cm.exception))
        self.assertFalse([c for c in fake.calls if c[1:2] == ["connect"]])

    def test_connect_failure_is_phone_unreachable(self):
        fake = self.fake(connect_ok=False)
        s, events = self.src(fake)
        with self.assertRaises(DeviceUnavailable) as cm:
            s.fetch()
        self.assertEqual(MSG_UNREACHABLE, str(cm.exception))
        self.assertEqual(["reconnect_attempt"], events)

    def test_connected_but_still_failing_is_unreachable_and_bounded_to_one_reconnect(self):
        fake = self.fake()
        orig = fake.__call__

        def stubborn(argv, timeout):
            res = orig(argv, timeout)
            if "connect" in argv[1:3]:
                return res                                     # connect "succeeds" but the phone never answers
            return RunResult(1, "", "adb: device offline\n") if argv[-2:-1] == ["shell"] else res

        s, _ = self.src(stubborn)
        with self.assertRaises(DeviceUnavailable) as cm:
            s.fetch()
        self.assertEqual(MSG_UNREACHABLE, str(cm.exception))
        self.assertEqual(1, sum(1 for c in fake.calls if "connect" in c[1:3]), "one reconnect per fetch")
        self.assertEqual(2, sum(1 for c in fake.calls if c[-2:-1] == ["shell"]), "the query is tried once, retried once")

    def test_a_timeout_on_a_network_target_also_recovers(self):
        fake = self.fake(reachable=None)
        orig = fake.__call__
        state = {"first": True}

        def hang_once(argv, timeout):
            if argv[-2:-1] == ["shell"] and state["first"]:
                state["first"] = False
                raise DeviceUnavailable("adb timed out after 15s")
            return orig(argv, timeout)

        s, events = self.src(hang_once)
        self.assertEqual(8, len(s.fetch()))
        self.assertEqual(["reconnect_attempt", "reconnected"], events)

    def test_unauthorized_wireless_is_not_retried_and_says_what_to_do(self):
        fake = self.fake(state="unauthorized")
        s, events = self.src(fake)
        with self.assertRaises(DeviceUnavailable) as cm:
            s.fetch()
        self.assertIn("unauthorized", str(cm.exception))
        self.assertIn("wireless debugging", str(cm.exception))
        self.assertEqual([], events)
        self.assertEqual(1, len(fake.calls))

    def test_permission_denied_is_not_retried(self):
        fake = self.fake(reachable=None, deny=True)
        s, events = self.src(fake)
        with self.assertRaises(PermissionDenied):
            s.fetch()
        self.assertEqual([], events)

    def test_the_reconnect_budget_is_enforced(self):
        t = {"now": 0.0}
        fake = self.fake()
        orig = fake.__call__

        def slow(argv, timeout):
            if "mdns" in argv[1:3]:
                t["now"] += 11.0                               # `adb mdns services` ate the whole 10 s budget
            return orig(argv, timeout)

        s, _ = self.src(slow, clock=lambda: t["now"])
        with self.assertRaises(DeviceUnavailable) as cm:
            s.fetch()
        self.assertEqual(MSG_UNREACHABLE, str(cm.exception))
        self.assertFalse([c for c in fake.calls if "connect" in c[1:3]])

    def test_hostport_serial_reconnects_without_mdns(self):
        fake = FakeAdb(adb_rows(fixture_messages("adb")), serial=ADDR, reachable=set())
        s = AdbSource(serial=ADDR, adb_path="adb", runner=fake)
        self.assertEqual(8, len(s.fetch()))
        self.assertFalse([c for c in fake.calls if "mdns" in c[1:3]])
        self.assertTrue([c for c in fake.calls if "connect" in c[1:3]])

    def test_usb_serial_never_runs_mdns_or_connect(self):
        fake = FakeAdb([], serial="ABC123", reachable=set())
        with self.assertRaises(DeviceUnavailable):
            AdbSource(serial="ABC123", adb_path="adb", runner=fake).fetch()
        self.assertFalse([c for c in fake.calls if c[1:3] and ("mdns" in c[1:3] or "connect" in c[1:3])])

    def test_connect_argument_is_validated(self):
        fake = self.fake(advertised={NAME: "$(reboot):1"})
        s, _ = self.src(fake)
        with self.assertRaises(DeviceUnavailable):
            s.fetch()
        self.assertFalse([c for c in fake.calls if "connect" in c[1:3]])

    def test_no_silent_empty_result_on_failure(self):
        fake = self.fake(connect_ok=False)
        s, _ = self.src(fake)
        with self.assertRaises(DeviceUnavailable):
            s.fetch()                                          # raises; never returns []


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
