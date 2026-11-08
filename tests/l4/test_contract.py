"""docs/CONTRACT.md surface for line.sms, and the consent stub's behaviour against the contract text."""
import inspect
import unittest

import line.sms as sms
from line.sms.consent import OTP_ACTION


class ContractSurface(unittest.TestCase):
    def test_names_the_contract_promises(self):
        for name in ("AdbSource", "GatewaySource", "FixtureSource", "OtpMatcher", "SecretText", "release_otp",
                     "Destination", "approval_prompt", "CALL_TRANSCRIPT_MARKER"):
            self.assertTrue(hasattr(sms, name), name)

    def test_release_otp_signature(self):
        params = list(inspect.signature(sms.release_otp).parameters)
        self.assertEqual(["task_id", "consent_token", "consent_store", "timeout_s", "approval_generation"], params)
        self.assertEqual(inspect.Parameter.KEYWORD_ONLY, inspect.signature(sms.release_otp).parameters["timeout_s"].kind)

    def test_call_marker_is_the_contract_text(self):
        self.assertEqual("[verification code sent]", sms.CALL_TRANSCRIPT_MARKER)

    def test_stub_consent_store_is_gone(self):
        import line.sms.consent as consent

        self.assertFalse(hasattr(consent, "StubConsentStore"))

    def test_sources_share_one_interface(self):
        for cls in (sms.AdbSource, sms.GatewaySource, sms.FixtureSource):
            self.assertTrue(issubclass(cls, sms.SmsSource), cls)


class RealConsentStore(unittest.TestCase):
    """Our binding against L2's `line.policy.consent.ConsentStore` (the stub is gone)."""

    def setUp(self):
        from line.policy.consent import ConsentStore
        from line.sms import ApprovedTask, binding_for

        from .helpers import FILL_DEST

        self.t = 1000.0
        self.store = ConsentStore(clock=lambda: self.t)
        self.task = ApprovedTask("t", ("ACME",), 1, 2, destination=FILL_DEST)
        self.binding = binding_for(self.task)

    def test_single_use_and_expiry(self):
        tok = self.store.issue(OTP_ACTION, "t", self.binding, 60)
        self.assertTrue(self.store.consume(tok, OTP_ACTION, "t", self.binding))
        self.assertFalse(self.store.consume(tok, OTP_ACTION, "t", self.binding))
        tok = self.store.issue(OTP_ACTION, "t", self.binding, 60)
        self.t += 61
        self.assertFalse(self.store.consume(tok, OTP_ACTION, "t", self.binding))

    def test_mismatch_is_false_and_spends_the_token(self):
        """L2 semantics: any consume that finds the token spends it. L4 therefore consumes only when a
        code is about to be released (service.OtpService), never to probe."""
        tok = self.store.issue(OTP_ACTION, "t", self.binding, 60)
        other = dict(self.binding, destination={"kind": "fill", "origin": "https://evil.example.net"})
        self.assertFalse(self.store.consume(tok, OTP_ACTION, "t", other))
        self.assertFalse(self.store.consume(tok, OTP_ACTION, "t", self.binding))

    def test_binding_is_json_clean_and_key_order_free(self):
        import json

        json.dumps(self.binding)
        tok = self.store.issue(OTP_ACTION, "t", self.binding, 60)
        shuffled = dict(reversed(list(self.binding.items())))
        self.assertTrue(self.store.consume(tok, OTP_ACTION, "t", shuffled))

    def test_ttl_limit_is_the_stores(self):
        with self.assertRaises(ValueError):
            self.store.issue(OTP_ACTION, "t", self.binding, 3601)


if __name__ == "__main__":
    unittest.main()
