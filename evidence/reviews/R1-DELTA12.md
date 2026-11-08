# R1 delta 12 — keypad confirmation and rolling PIN scrub

Reviewed main **8d9141f20b8cb36e8523425e963407e9a4a86c8c**, including L1 **05e0257**, with `docs/lanes/L1.md` section 24.

**Verdict: YES for the specified single attended owner-line trial, with OTP off, ConversationRelay, keypad-only PIN/confirmation and the existing strict allowlist containing only the owner's Spectrum and Fi numbers.** Those numbers have different suffixes ([owner phone (Spectrum)] and [owner phone (Fi)]). This is scoped security clearance, not approval of unrestricted owner-line operation or proof of live telephony behavior. Do not broaden the destination allowlist on the strength of this verdict.

The former High spoken-approval failure is closed: speech no longer approves. The original immediate split-PIN disclosure probes also pass. There remain two Medium limitations for broader use, detailed below: last-four collisions do not identify a unique proposal, and late spoken PIN fragments cannot be recalled from Hermes. Neither is a blocker for this one-call, two-distinct-number, keypad-only trial. No Critical/High finding was confirmed in this delta's specified trial scope.

References are repository-relative paths in the exact exported snapshot under `disposable/`. The allowlist prerequisite is grounded in the existing harness configuration (`tools/trial/run.py:32,163–170`), not inspection of private runtime settings. I did not run or fully re-review the live trial harness, provisioning, forwarding, billing or restoration workflow in this delta.

## N12 — original speech vulnerability fixed; last-four confirmation has a collision limitation

`plugin/line/engine/machine.py:419–424` sends DTMF to the confirmation handler only on an already PIN-verified session with a current proposal. The speech path no longer calls an approval handler (`machine.py:456–515`). The old held-read-back/interruption/“Yes” probe now creates no approval ticket. Repeating “yes,” saying the destination suffix or injecting ordinary spoken audio does not itself call `voice_approve`.

The confirmation handler compares the keyed sequence against the proposal's suffix and checks the monotonic 60-second deadline (`machine.py:848–888`). A wrong entry invalidates the proposal; timeout invalidates it; acceptance clears it and records a single ticket bound to the full task/destination. Existing service ticket consumption still checks the task and full canonical destination before issuing the ordinary dial consent. A replacement resets the key buffer and cancels the old timeout. These checks are meaningful, but the digits themselves are neither random nor unique to a generation.

### Which leg supplies the digits?

The server accepts relay frames through the claimed call's websocket (`server/ingress.py:294–327`). The consumer checks the current link and successful setup (`machine.py:385–399`); setup correlates the CallSid (`machine.py:430–438`). PIN verification is session state initialized false, not transferred to later calls. Outbound dialing creates a fresh CallSession, so the business/callee leg does not inherit owner verification. Its DTMF cannot confirm a proposal in the terminated inbound owner session. The separately rung owner leg uses the existing press-1 bridge route, not this confirmation handler.

This is leg authentication, not cryptographic proof that a physical keypad was pressed. A party able to inject provider-recognized DTMF onto the authenticated owner's leg can supply the same events. Ordinary speech frames cannot. Whether a particular handset/carrier converts injected in-band tones into DTMF events was not tested offline. Someone with the capability to forge frames using compromised ingress capabilities also inherits the previously documented fallback limitations; the new confirmation does not repair them. I found no new route allowing a later callee's frames to be accepted as this owner session's frames.

### Early digits and collisions — Medium residual for broader owner mode

**Evidence:** `machine.py:819–825` installs the proposal/challenge before `_after_turn` sends its read-back at lines 890–893. `_confirm_check` at lines 859–878 checks suffix/deadline, not read-back delivery or the frame's arrival generation. DTMF handling does not use the recorded ingress timestamp. The challenge is simply `last_four(destination)` (`owner_line.py`, function `last_four`).

Digits processed before any proposal are ignored, as the regression test establishes. Digits processed **after proposal creation but before read-back** are accepted. Already queued frames have no proposal-generation binding either. Thus early entry is intentionally possible, and the keypad mechanism does not prove audibility.

**Confirmed reproduction:** in a synthetic allowlist, propose destination A, then replace it with distinct destination B having the same last four digits, while withholding read-back. Key A's suffix and `#`. The service issues the ticket for B. The probe uses only fake destinations/adapters. This disproves section 24's unqualified statement that an earlier request's digits no longer count after replacement. The stored ticket is correctly bound to B; the problem is that the owner's input does not distinguish A from B. Same-number replacement of a materially different task likewise cannot be distinguished by the suffix alone.

**Smallest fix before general use:** confirm a fresh per-proposal challenge delivered at the end of the full fixed read-back, or confirm through the existing owner surface. At minimum, require full-number entry for number collisions and invalidate/rechallenge on replacement; add stale queued-input and same-suffix replacement tests. A buffer reset alone does not distinguish delayed old input from fresh input.

**Why this does not block this trial:** the authorized trial has one requested outbound destination and a strict two-number allowlist whose suffixes differ. Keying Fi's [owner phone (Fi)] cannot authorize Spectrum's [owner phone (Spectrum)], and a colliding third destination is rejected by that policy. This is substantially stronger for this narrow trial than the old generic “yes.” This judgment does not claim the general collision is fixed.

## N14 — immediate split probes fixed; late-fragment disclosure remains

The adapted probes now verify that adjacent `four eight` / `two one` frames and `four eight; two one` become `[PIN]`, without the literal fragments entering Hermes or the transcript. Matching now uses original frame fragments across punctuation, and recognizes a PIN with any one digit omitted (`owner_line.py:64–162`). Keypad entry still bypasses speech persistence and never sends the PIN to Hermes.

On owner-number inbound sessions, `machine.py:459–505` holds a possible PIN prefix for up to 1.5 seconds, matches a rolling window, and rewrites contributing transcript rows if necessary. Other calls retain single-frame scrubbing. This is bounded buffering, not a guarantee that every conceivable spoken PIN is secret.

**Documented Medium residual reproduced:** send the first two digits as words, wait until the 1.5-second hold releases them to Hermes, then send the remaining two. The first fragment remains in the Hermes driver's turns. The store rows become `[PIN]` and `pin_redacted_late` is emitted. Rewriting a transcript does not recall earlier model input, intermediate snapshots or previously exported evidence. My probe confirms model-history retention and store redaction; it does not claim that every other sink was independently exercised.

The documentation's “transcript row is still redacted” also depends on the first fragment remaining in the rolling window: completed fragments expire after ten seconds and only a few frames are retained (`machine.py:473–476`); three waiting frames can force a flush. Longer gaps/eviction need not receive that later repair. The recorded originals remain in memory temporarily to perform matching. These bounds should be described alongside the 1.5-second residual.

**Smallest fix if a no-spoken-PIN-leak guarantee is required:** suppress digit-like speech during authentication rather than forwarding it after a deadline, with an explicit exit to ordinary conversation. A bounded hold cannot guarantee reconstruction before disclosure. A general guarantee also requires sanitization at notes/events/export boundaries; section 24 correctly acknowledges those are not all PIN-scrubbed.

**Trial judgment:** this residual does not block the explicitly keypad-only trial. The prompt now requests keys, spoken PINs do not authenticate, and the owner's planned PIN input is DTMF. Do not treat that narrow clearance as assurance that a mistakenly spoken, slowly fragmented PIN remains secret. If a real PIN is accidentally spoken and parts reach model history, redacting the transcript is not sufficient remediation; replace the credential before relying on it again.

## Test coverage and results

The original delta-11 speech/timing probe was adapted to assert no approval, since timer arming no longer exists. The immediate split/semicolon probes were inverted to require masking. Separate assertions reproduced the collision and late-fragment limitations.

| Command | Result |
|---|---|
| `python -m unittest discover -s tests/l1 -p test_r13_owner_line.py -v` | **27 passed**, 13.063 s; `r13.log` |
| `python ../fixtures/probe_owner.py` | Exit 0; original cases pass, collision and late-fragment limitations confirmed; `probe.log` |

Interpreter: `/Users/mac/.hermes/hermes-agent/venv/bin/python`; working directory: `disposable/`. `run_checks.py`, `runner_prefix.py` and the synthetic fixture are retained beside the report.

The suite tests keyed single-use approval, wrong digits, timeout, speech refusal, replacement with a different suffix, pre-proposal keys, immediate split/semicolon masking, late store redaction, and ordinary speech. It does **not** test same-suffix replacement, queued old-generation keys, provider recognition of injected audio tones, or real playback. “Digits before the proposal” is not a test of digits before read-back. The existing late-fragment test explicitly accepts that model input cannot be recalled. No full historical L1/L2/L4 rerun or new end-to-end live bridge claim is made here; destination/owner-ring consent and press-1 routes remain as assessed previously.

## Boundaries

The checkout and git remained unchanged. I exported the exact main revision to this scratch directory. All target execution used the prior offline sandbox: external networking denied, loopback permitted, scratch-only writes, clean environment/scratch HOME and TMPDIR, bytecode disabled, resource/wall-time bounds and an explicit denial of the real `/Users/mac/.hermes-line`. Host loopback was used without a separate namespace or hard memory limit. No real credential, provider, phone, gateway, tunnel or private runtime setting was accessed.

This review authorizes no live action. Its YES is limited to the specified attended keypad-only trial and the two-phone allowlist; general owner-mode confirmation and universally secret spoken PIN handling remain incomplete as described above.
