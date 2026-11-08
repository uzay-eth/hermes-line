# L4 round 7 (coordinator): Android 17 code delay, live readiness

`git merge main`. Owner step 4 is: at a time the coordinator gives, Can asks a service to text a login code to the
Fi line (Pixel on USB). New fact: Twilio numbers do not receive one-time-code texts (error 30038), so the Pixel is
the only code source. Build `tests/l4/live/otp_delay.py` (no plugin code change unless a bug shows): watches the
Pixel's SMS inbox over adb (read only) for N minutes, and for each new message records the SMS provider timestamps
(`date`, `date_sent`), the time our reader first saw it, the sender masked and whether a code-like token was
present, never the body or the code. It prints a one-line summary per message and writes masked JSON to
`evidence/sms/otp-delay-<utc>.json`. Also measure the plugin's own reader latency on that same message (call
`plugin/line/sms` read path once per poll). Dry-run it now for 2 minutes against the real Pixel (read only; if adb
says unauthorized, stop and report). LIMITS as your brief; reading the Pixel inbox is allowed; nothing is sent.
DONE: dry-run output (masked), tests, SHA, exact command for the coordinator.
