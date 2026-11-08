# Brief L4: SMS and OTP release in the plugin

## PURPOSE
Hermes Line lets the user's Hermes agent read SMS verification codes that arrive on the user's real mobile line, but only for a task the owner approved, with the code masked everywhere except the one approved tool result. The S3 spike proved the pieces; this lane turns them into the plugin module and closes the leak paths.

## TASK
Read `docs/BRIEF.md` (verify sha256 346bbac78d9e7054ca55985ed0181b13449d2b7fa359cb593d870ad9d3eb167d, else stop), `AGENTS.md`, `docs/CONTRACT.md`, `docs/spikes/S3-pixel-sms.md`, ADR 0002 items 7-8 and `spikes/s3-sms/`.
1. Port `spikes/s3-sms/` into `plugin/line/sms/` with the contract interface, including `release_otp`.
2. Audit the Hermes core tool-result persistence path (session history, transcripts, logs, gateway delivery, Desktop broadcasts) in `/Users/mac/.hermes/hermes-agent` (read only) with file:line citations, and design the one approved cleartext delivery so the code does not persist in history. If it cannot be done without a core change, say so and propose the safest alternative.
3. Tests: masking on every log and transcript path the module touches; single-use consent; wrong sender, wrong window, two codes, replay.

## LIMITS
- No phone, no calls, no Twilio, no network, no purchases.
- Read Hermes source; do not edit it.
- Never print or store a real message body or code unmasked.
- Write only `plugin/line/sms/`, `tests/l4/` and `docs/lanes/L4.md`. Commit locally on your branch; no push.
- Use the consent store interface from the contract; stub it until L2 merges.
- Owner decisions: if you need a decision from Can (the owner), do not ask Can and do not write a decision block yourself. Stop at that point and put the question in your final report to the coordinator: the options (A, B, ...), your recommendation and a one-line reason. Only the coordinator posts owner-decision blocks.

## DONE
- Test output for `tests/l4/`.
- `docs/lanes/L4.md`: the persistence audit with citations, the chosen release design, residual leak risks.
- Final report: commit SHA, test counts, open risks.
