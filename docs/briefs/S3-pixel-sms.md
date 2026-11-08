# Brief S3: Pixel SMS spike and OTP masking

## PURPOSE
Hermes Line lets the user's Hermes agent read SMS verification codes that arrive on the user's real mobile line, only for a task the owner approved, with the code masked everywhere else. Can's line is a Google Fi number on an unlocked Pixel 10 Pro XL that will sit on the Mac by USB. This spike proves how we read SMS from that phone and builds the OTP extraction and masking code the plugin will use.

## TASK
Read `docs/BRIEF.md` (verify sha256 346bbac78d9e7054ca55985ed0181b13449d2b7fa359cb593d870ad9d3eb167d, else stop) and `AGENTS.md`.
1. Install Android platform-tools on the Mac with Homebrew if `adb` is missing (free, open source).
2. Research, with sources: does `adb shell content query --uri content://sms/inbox` work as the shell user on the Android version a Pixel 10 Pro XL runs today? Which columns and filters (date, address) work?
3. If a device shows in `adb devices`, run the read-only probe and record the result with every message body masked. If no device is attached, prepare the probe script and stop that part with "waiting for owner step".
4. Fallback: compare maintained open-source on-device SMS gateway apps with a local API (licence, last release, local-only mode, auth, push vs poll). Recommend one. Do not install it.
5. Build `spikes/s3-sms/`: an SMS source interface (adb source, gateway source, fixture source), OTP extraction (codes of 4-8 digits and common alphanumeric shapes, sender and time-window matching to an approved task), and a masking function used by every log and transcript path. Unit tests with fixture messages, including tricky cases (phone numbers, dates, amounts, two codes in one message, non-English text).

## LIMITS
- Read only on the phone: no settings changes, no app installs, no messages sent, no calls.
- Never print or store a real message body or code unmasked, in logs, test output or commits.
- No phone calls, no Twilio, no purchases, no API keys.
- Write only under `spikes/s3-sms/` and `docs/spikes/S3-*.md`. Commit locally on your branch; no push.
- Tests run offline.
- Owner decisions: if you need a decision from Can (the owner), do not ask Can and do not write a decision block yourself. Stop at that point and put the question in your final report to the coordinator: the options (A, B, ...), your recommendation and a one-line reason. Only the coordinator posts owner-decision blocks.

## DONE
- `docs/spikes/S3-pixel-sms.md`: the adb answer with sources, probe output (masked) or "waiting for owner step", the gateway comparison and recommendation.
- `spikes/s3-sms/` with passing tests; test output pasted in the spike doc.
- Final report: commit SHA, the adb answer, the recommended fallback, open risks.
