# s3-sms: read SMS verification codes for an approved task, mask them everywhere else

Spike S3 for Hermes Line. Findings are in [`docs/spikes/S3-pixel-sms.md`](../../docs/spikes/S3-pixel-sms.md).
Stdlib only, Python 3.9+, tests run offline.

```
smsline/
  secret.py        SecretText: repr/str/format print "<redacted>"; no pickle/JSON; .reveal() is the audit surface
  masking.py       redact_all (shape only), mask_codes ([CODE]), mask_sender, MaskingFilter for logging
  textnorm.py      1:1 normalisation: fullwidth/Arabic-Indic digits, NBSP, zero-width characters
  model.py         SmsMessage (body is a SecretText; repr uses the masked summary), sender normalisation
  otp.py           extract_otp(): 4-8 digit, grouped, letter-prefixed and uppercase alnum codes, scored by context
  matching.py      ApprovedTask + TaskMatcher (sender AND window AND unambiguous AND single-use), wait_for_code()
  sources/
    base.py        SmsSource interface, errors, apply_filters
    adb.py         AdbSource: `adb shell content query` (SELECT only), hardened parser, id cross-check
    gateway.py     GatewaySource: capcom6 SMS Gateway local API (GET /inbox, Basic auth, loopback http only)
    fixture.py     FixtureSource: offline, can model a provider that withholds OTP messages
probe.py           read-only probe for the real phone: check | inbox | watch (never prints a body)
tests/             115 tests + fixtures (synthetic messages only)
run_tests.sh       python3 -m unittest discover -s tests -t . -v
```

## Use

```python
from smsline import AdbSource, ApprovedTask, TaskMatcher, wait_for_code, Decision, install_log_masking

install_log_masking()                                   # defence in depth; the library never logs bodies anyway
task = ApprovedTask("task-17", ("ACME", "32665"), window_start_ms=now_ms, window_end_ms=now_ms + 5 * 60_000)
out = wait_for_code(AdbSource(), TaskMatcher(task), timeout_s=120)
if out.decision is Decision.RELEASE:
    code = out.release()        # the single approved read; everything else is masked
else:
    log.info(out.log_line())    # sender masked, no body, no code
```

## Rules the code enforces (each has a test)

* A message body is never printable: `SmsMessage.body` is a `SecretText`; repr/str/format/f-string, `%s`, pickle and JSON all fail closed.
* `.reveal()` has four call sites (listed in `tests/test_no_leak.py::RevealAudit`); only `MatchOutcome.release()` returns a code to a caller.
* Log lines carry `SmsMessage.log_summary()` (masked sender, UTC time, length) and never text. `redact_all` gives the body's shape for debugging; `mask_codes` is for owner-facing transcripts and cannot see letter-only codes, so bodies are never logged through it.
* Release needs: sender rule match, message time inside the approved window (start widened by a clock-skew allowance), exactly one winning code (two competing codes means `AMBIGUOUS`, never a guess), and the message not already used.
* adb access is SELECT-only (`content query`, `getprop`), checked by an allow-list and a test. The adb output parser rejects forged `Row:` lines inside message bodies via a second `_id`-only query.

## Probe on the real phone (owner step first)

```
python3 probe.py check                      # exit 3 + "waiting for owner step" until the Pixel is attached and authorised
python3 probe.py inbox --since-minutes 120  # masked listing: id, masked sender, time, length, shape, OTP found
python3 probe.py watch --duration 900       # lag between the phone's timestamp and first visibility, per message
```
