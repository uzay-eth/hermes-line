import unittest

from smsline.otp import extract_otp
from smsline.secret import SecretText

from tests.helpers import load_otp_cases


class OtpCaseTable(unittest.TestCase):
    """Table-driven: every row of fixtures/otp_cases.json."""

    def test_cases(self):
        for case in load_otp_cases():
            with self.subTest(case["name"]):
                res = extract_otp(case["body"])
                got = sorted(c.value.reveal() for c in res.candidates)
                self.assertEqual(sorted(case["codes"]), got, "accepted candidates differ")
                self.assertEqual(bool(case.get("ambiguous", False)), res.ambiguous)
                if len(case["codes"]) == 1:
                    self.assertIsNotNone(res.primary)
                    self.assertEqual(case["codes"][0], res.primary.value.reveal())
                else:
                    self.assertIsNone(res.primary, "no primary when empty or ambiguous")


class OtpDetails(unittest.TestCase):
    def test_accepts_secrettext_and_str(self):
        a = extract_otp("Your code is 482915")
        b = extract_otp(SecretText("Your code is 482915"))
        self.assertEqual(a.primary.value, b.primary.value)

    def test_candidate_repr_never_shows_value(self):
        res = extract_otp("Your code is 482915")
        text = repr(res) + str(res.primary) + repr(res.primary.value) + repr(res.primary.printed)
        self.assertNotIn("482915", text)

    def test_kinds(self):
        self.assertEqual("digits", extract_otp("Your code is 482915").primary.kind)
        self.assertEqual("grouped", extract_otp("Code: 482 915").primary.kind)
        self.assertEqual("prefixed", extract_otp("G-482915 is your Google verification code.").primary.kind)
        self.assertEqual("alnum", extract_otp("Your ACME code is AB12CD").primary.kind)

    def test_printed_form_kept_for_prefixed_and_grouped(self):
        self.assertEqual("G-482915", extract_otp("G-482915 is your Google verification code.").primary.printed.reveal())
        self.assertEqual("482-915", extract_otp("Code: 482-915").primary.printed.reveal())

    def test_reasons_never_contain_content(self):
        res = extract_otp("Your code is 482915 and unit 77")
        for c in res.candidates:
            for r in c.reasons:
                self.assertNotIn("482915", r)

    def test_ambiguity_has_no_primary_but_lists_both(self):
        res = extract_otp("Your code is 482915. Backup code: 654321")
        self.assertTrue(res.ambiguous)
        self.assertIsNone(res.primary)
        self.assertFalse(res.has_code)
        self.assertEqual({"482915", "654321"}, {c.value.reveal() for c in res.candidates})

    def test_threshold_is_a_real_gate(self):
        # Raising the bar drops the bare-number message the default accepts.
        self.assertTrue(extract_otp("ACME: 482915").has_code)
        self.assertFalse(extract_otp("ACME: 482915", accept_score=6.0).has_code)

    def test_very_long_body_does_not_blow_up(self):
        body = ("lorem ipsum 12 " * 200) + " code 482915"   # ~3000 chars, under MAX_ANALYSED_CHARS
        self.assertEqual("482915", extract_otp(body).primary.value.reveal())

    def test_regexes_are_linear_enough(self):
        import time

        nasty = "1-" * 5000 + "A" * 5000 + " " + "9 " * 5000
        t = time.time()
        extract_otp(nasty)
        self.assertLess(time.time() - t, 2.0)


if __name__ == "__main__":
    unittest.main()


class AdversarialInput(unittest.TestCase):
    """Bodies are attacker-controlled: no input shape may make analysis or masking slow.

    Regression: the first e-mail pattern (\\S+@\\S+\\.\\S+) took ~3 s on 1600 '@' characters
    and grew cubically; a look-ahead mixed-token mask regex was quadratic on long runs.
    """

    SHAPES = ["@", "a@", "x@y", "a.b@", "1", "1-", "A1", "A1-", "AAAAA-", " ", "+1 ", "$1,",
              "٤", "code ", "http://", "www.", "1 ", "12 34 56 ", "(555) "]

    def test_worst_case_time_is_bounded(self):
        import time
        from smsline.masking import mask_codes, redact_all

        for n in (1600, 4000, 20000, 100000):
            for shape in self.SHAPES:
                s = (shape * (n // len(shape) + 1))[:n]
                for fn in (extract_otp, mask_codes, redact_all):
                    t = time.time()
                    fn(s)
                    took = time.time() - t
                    self.assertLess(took, 1.5, "%s on %r*%d took %.2fs" % (fn.__name__, shape, n, took))

    def test_overlong_body_yields_no_code(self):
        body = "Your code is 482915. " + "x" * 5000
        self.assertFalse(extract_otp(body).has_code)

    def test_mask_codes_still_masks_in_a_long_body(self):
        from smsline.masking import mask_codes
        out = mask_codes("code 482915 " + "filler " * 3000 + " and AB12CD")
        self.assertNotIn("482915", out)
        self.assertNotIn("AB12CD", out)
