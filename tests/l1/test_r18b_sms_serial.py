"""After round 18: L4 round 8 (wireless adb) settings. ``sms_adb_serial`` (USB serial, wireless-debugging mDNS service
name or ip:port; empty = the one USB phone) travels from the plugin's config to ``AdbSource(serial=...)``."""
import unittest
from pathlib import Path
from unittest import mock

import yaml

from plugin.line import sms
from plugin.line.engine import deps, hermes_glue
from plugin.line.sms import hooks as sms_hooks
from test_otp_and_plugin import FakeCtx

MANIFEST = Path(__file__).resolve().parents[2] / "plugin" / "plugin.yaml"


class SerialReachesTheSourceTests(unittest.TestCase):
    def wire(self, config):
        """Run the plugin's OTP wiring from a plugin config; return every AdbSource it built."""
        built = []
        real = sms.AdbSource

        def recording(*args, **kwargs):
            source = real(*args, **kwargs)
            built.append((args, kwargs, source))
            return source
        ctx = FakeCtx(config)
        host = hermes_glue._Host(ctx)
        with mock.patch.object(sms, "AdbSource", recording), mock.patch.object(sms, "configure", lambda otp: None), \
                mock.patch.object(sms_hooks, "register", lambda *a, **kw: None):
            hermes_glue._register_otp(ctx, host)
        return built, host

    def setUp(self):
        if deps.sms.is_stub:
            self.skipTest("line.sms not present in this tree")

    def test_a_wireless_debugging_service_name_reaches_the_source(self):
        built, host = self.wire({"sms_source": "adb", "sms_adb_serial": "adb-5B050DLCQ000P3-eQkDjp"})
        self.assertEqual(len(built), 1)
        args, kwargs, source = built[0]
        self.assertEqual(kwargs.get("serial"), "adb-5B050DLCQ000P3-eQkDjp")
        self.assertEqual(source.kind, "mdns")
        self.assertEqual(source.serial, "adb-5B050DLCQ000P3-eQkDjp._adb-tls-connect._tcp")
        self.assertIs(host.otp.source, source)  # the OTP service reads from exactly that phone

    def test_ip_port_and_usb_serials_reach_the_source(self):
        for value, kind in (("192.168.1.40:37123", "hostport"), ("5B050DLCQ000P3", "usb")):
            built, _ = self.wire({"sms_source": "adb", "sms_adb_serial": value})
            self.assertEqual(built[0][1].get("serial"), value)
            self.assertEqual(built[0][2].kind, kind)

    def test_empty_means_the_one_usb_phone(self):
        built, _ = self.wire({"sms_source": "adb"})
        self.assertIsNone(built[0][1].get("serial"))
        self.assertEqual(built[0][2].kind, "none")  # adb's own "the one attached device"

    def test_sms_source_none_builds_no_source(self):
        built, host = self.wire({"sms_source": "none", "sms_adb_serial": "5B050DLCQ000P3"})
        self.assertEqual(built, [])
        self.assertIsNone(getattr(host, "otp", None))


class ManifestTextTests(unittest.TestCase):
    def test_settings_text_is_l4_round_8s(self):
        schema = yaml.safe_load(MANIFEST.read_text())["config_schema"]
        self.assertIn("USB or Wi-Fi wireless debugging", schema["sms_source"]["description"])
        serial = schema["sms_adb_serial"]["description"]
        for part in ("USB serial", "wireless-debugging service name", "ip:port", "Empty means the one USB phone",
                     "only that phone is used"):
            self.assertIn(part, serial)
        self.assertEqual(schema["sms_adb_serial"]["default"], "")


if __name__ == "__main__":
    unittest.main()
