import copy
import io
import json
import logging
import pickle
import unittest

from smsline import SecretText, SmsMessage, install_log_masking, mask_codes, mask_sender, redact_all
from smsline.masking import CODE_TOKEN, MaskingFilter, mask_handler

from tests.helpers import T0, load_otp_cases


class SecretTextTests(unittest.TestCase):
    def setUp(self):
        self.s = SecretText("hunter2 code 482915")

    def test_every_formatting_path_is_redacted(self):
        outputs = [
            repr(self.s), str(self.s), "%s" % self.s, "%r" % self.s, "{}".format(self.s),
            f"{self.s}", f"{self.s!r}", format(self.s, ">30"), repr([self.s]), repr({"k": self.s}),
            str((self.s,)),
        ]
        for o in outputs:
            self.assertNotIn("482915", o)
            self.assertNotIn("hunter2", o)

    def test_reveal_is_the_only_way_out(self):
        self.assertEqual("hunter2 code 482915", self.s.reveal())

    def test_no_serialisation(self):
        with self.assertRaises(TypeError):
            pickle.dumps(self.s)
        with self.assertRaises(TypeError):
            json.dumps({"x": self.s})
        self.assertIs(self.s, copy.copy(self.s))
        self.assertIs(self.s, copy.deepcopy(self.s))

    def test_immutable_and_typed(self):
        with self.assertRaises(AttributeError):
            self.s._v = "other"
        with self.assertRaises(TypeError):
            SecretText(123)

    def test_equality_and_bool(self):
        self.assertEqual(SecretText("a"), SecretText("a"))
        self.assertNotEqual(SecretText("a"), SecretText("b"))
        self.assertFalse(SecretText(""))
        self.assertTrue(self.s)


class RedactAllTests(unittest.TestCase):
    def test_shape_only(self):
        self.assertEqual("A a a 9.", redact_all("Your code is 482915."))

    def test_nothing_of_the_original_survives_in_any_language(self):
        for case in load_otp_cases():
            red = redact_all(case["body"], max_len=500)
            for c in case["codes"]:
                self.assertNotIn(c, red)
            # no letter or digit of the source: only a/A/9, punctuation, spaces, dots
            self.assertTrue(set(red) <= set("aA9 .,:;!?-_()[]<>#$%&*+/=@'\"\\{}|~^`\u2026"), repr(red))

    def test_truncation(self):
        r = redact_all("word " * 100, max_len=20)
        self.assertEqual(20, len(r))
        self.assertTrue(r.endswith("\u2026"))

    def test_empty(self):
        self.assertEqual("", redact_all(""))


class MaskCodesTests(unittest.TestCase):
    def test_codes_are_replaced_and_context_stays(self):
        self.assertEqual("Your code is [CODE]. Do not share.", mask_codes("Your code is 482915. Do not share."))

    def test_length_is_hidden(self):
        self.assertEqual(mask_codes("code 1234"), mask_codes("code 12345678"))

    def test_every_planted_code_is_masked_in_every_case(self):
        for case in load_otp_cases():
            masked = mask_codes(case["body"])
            for code in case["codes"]:
                with self.subTest(case["name"]):
                    self.assertNotIn(code, masked)
                    # also not hidden by a separator or fullwidth/Arabic digits:
                    self.assertEqual(
                        [], [c for c in (code,) if c in masked.replace(" ", "").replace("-", "")]
                    )

    def test_small_numbers_and_words_survive(self):
        self.assertEqual("Valid for 10 minutes, room 42", mask_codes("Valid for 10 minutes, room 42"))

    def test_phone_numbers_and_dates_are_masked_too(self):
        out = mask_codes("Call 800-555-0100 on 2026-11-04")
        self.assertNotIn("555", out)
        self.assertNotIn("2026", out)

    def test_alnum_codes(self):
        self.assertEqual("Use [CODE] now", mask_codes("Use AB12CD now"))
        self.assertEqual("Use [CODE] now", mask_codes("Use X7K-9QP2 now"))

    def test_obfuscated_digits(self):
        self.assertNotIn("482915", mask_codes("code 482\u200b915"))
        self.assertNotIn("\u0664\u0668", mask_codes("\u0664\u0668\u0662\u0669\u0661\u0665"))
        self.assertEqual(CODE_TOKEN, mask_codes("\uff14\uff18\uff12\uff19\uff11\uff15"))

    def test_known_limit_letter_only_code_is_not_masked(self):
        # Documented: this is why logs use log_summary()/redact_all, never mask_codes, for bodies.
        self.assertIn("KJHDW", mask_codes("your code is KJHDW"))
        self.assertNotIn("KJHDW", redact_all("your code is KJHDW"))


class SenderMaskTests(unittest.TestCase):
    def test_phone_and_alnum(self):
        self.assertEqual("+***23", mask_sender("+15550100123"))
        self.assertEqual("A***ME", mask_sender("ACME"))
        self.assertEqual("E***NK", mask_sender("EXAMPLEBANK"))

    def test_short_codes_stay_readable(self):
        self.assertEqual("32665", mask_sender("32665"))

    def test_empty_and_tiny(self):
        self.assertEqual("", mask_sender(""))
        self.assertEqual("A***", mask_sender("AB"))


class LogMaskingTests(unittest.TestCase):
    def _logger(self, name):
        stream = io.StringIO()
        h = logging.StreamHandler(stream)
        lg = logging.getLogger(name)
        lg.handlers = [h]
        lg.propagate = False
        lg.setLevel(logging.DEBUG)
        lg.filters = []
        return lg, stream

    def test_filter_masks_message_and_args(self):
        lg, stream = self._logger("smsline")
        install_log_masking()
        lg.info("got code 482915 and %s", "654321")
        lg.info("secret object: %s", SecretText("482915"))
        out = stream.getvalue()
        self.assertNotIn("482915", out)
        self.assertNotIn("654321", out)
        self.assertIn(CODE_TOKEN, out)

    def test_install_is_idempotent(self):
        lg, _ = self._logger("smsline")
        install_log_masking()
        install_log_masking()
        self.assertEqual(1, sum(isinstance(f, MaskingFilter) for f in lg.filters))

    def test_handler_level_covers_child_loggers(self):
        stream = io.StringIO()
        h = mask_handler(logging.StreamHandler(stream))
        child = logging.getLogger("smsline.sources.adb.test")
        child.handlers = [h]
        child.propagate = False
        child.setLevel(logging.INFO)
        child.info("code 482915")
        self.assertNotIn("482915", stream.getvalue())

    def test_bad_format_args_do_not_crash_logging(self):
        lg, stream = self._logger("smsline")
        install_log_masking()
        lg.info("%d items", "not-a-number")   # logging would normally print a traceback
        self.assertNotIn("Traceback", stream.getvalue())


class MessageSurfaceTests(unittest.TestCase):
    def test_message_repr_and_summary_have_no_body(self):
        m = SmsMessage("7", "+15550100123", T0, "Your code is 482915. Hidden words here.")
        for text in (repr(m), str(m), m.log_summary(), "%s" % (m,), f"{m!r}"):
            self.assertNotIn("482915", text)
            self.assertNotIn("Hidden", text)
            self.assertNotIn("15550100123", text)
        self.assertIn("len=39", m.log_summary())

    def test_body_str_is_wrapped_automatically(self):
        m = SmsMessage("7", "x", T0, "plain str in")
        self.assertIsInstance(m.body, SecretText)


if __name__ == "__main__":
    unittest.main()
