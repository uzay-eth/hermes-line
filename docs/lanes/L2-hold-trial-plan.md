# Real airline hold-line trial — offline preparation

Status: L2 simulation passes; **live trial blocked on owner destination approval
and the L1/L6 prerequisites below**. This plan authorizes no call. Candidate
provided by the coordinator: Delta reservations, `+18002211212`. Its current
ownership/menu has not been independently verified in this offline lane.
One business call, ConversationRelay, owner mode; one separately approved owner
leg only when a human is detected. No redial or callback acceptance by default.

Source revision merged: `91f6cc8`. Brief SHA-256 reverified:
`346bbac78d9e7054ca55985ed0181b13449d2b7fa359cb593d870ad9d3eb167d`.
This document is the additional write path explicitly authorized for round 4.
No network, credentials, provider API, purchases or phone calls were used.

## Preconditions that prevent a first-attempt failure

1. Coordinator obtains approval for the exact business destination, owner leg,
   task, time window and spending envelope. Keep trial allowlist-only mode on;
   add only the approved candidate and owner through L1/L6 configuration.
   `blocked_destinations` must still contain the protected resource. Confirm the
   owner destination is available and cannot forward back into this call.
2. **L1 safety blocker:** `plugin/line/engine/machine.py:_maybe_confirm` currently
   calls `_on_human` after two unclear replies and emits `human_assumed` (around
   lines 537–544). In owner mode `_on_human` may ring the owner. Repeated junk
   recognition after a question can therefore bypass L2's UNKNOWN. Before this
   trial, L1 must disable that assumption for hold/owner mode and prove that
   UNKNOWN/music/announcements never ring the owner. L2 did not change L1 files.
   Also prevent unsolicited confirmation speech on known hold artifacts; the
   engine's `_words` filter is narrower than L2's artifact filter.
3. **L6 harness blocker:** `tools/trial/run.py:validate_call` allows only 1–3
   minutes and the existing own-line allowlist. `TrialAdapter._create_call`
   permits exactly one outbound create and requires `to == self.to` in live
   mode. Adding a destination alone will not permit the owner leg. L6 must add
   a purpose-specific approved airline trial scope: one business leg plus one
   separately approved owner leg, role-aware destinations, no retries, bounded
   remaining duration, complete two-leg/conference cleanup. Do not disable
   existing guards globally. Dry-run those exact forms before the live run.
4. L6 must implement/verify a global 45-minute deadline and ongoing budget guard,
   rather than relying on its before/after balance checks. A 45-minute provider
   cap on each leg separately does not bound the whole trial.
5. Load the exact task guidance in
   `fixtures/hold-sim/airline-agent-guidance.txt` into the agent's approved task
   instructions. The bundled skill/engine prompt was not edited (outside L2).
   Rehearse callback decline in the actual task/session: L2 labels callback
   offers IVR and supplies caution evidence, but the current L1 `_on_detection`
   passes the menu text, not Detection.evidence, to Hermes. Evidence text alone
   is therefore **not an enforced callback authorization gate**. L1 must carry
   the task restriction to its agent/tools and L6 verify no acceptance action.
6. Rehearse private owner whisper/acceptance, decline/timeout, business hangup
   during ringing and all-leg cleanup with the current engine. Verify provider
   auth, stable tunnel/reconnect handling and fresh funds in the authorized
   preflight. Use Relay throughout hold; no Grok voice session, raw audio sidecar
   or second speech provider is needed for this trial.

These are coordinator integration requirements, not questions to the owner from
L2. The worker does not broaden another lane's code scope to satisfy them.

## What Relay may deliver during hold

Basis: repository S2 telephony notes section 2 and the real handler
`CallSession._handle_frame` / `_detect`. Live airline hold traffic is unmeasured.
No assertion is made that Relay sends one mandatory music/silence representation.
It is a text recognition boundary, not an acoustic music classifier.

| Possible observation | Current handler / L2 interpretation | Required action |
|---|---|---|
| No prompt for seconds or minutes | No detector invocation; no evidence of a person | Keep the session listening; independently monitor connection health and deadline |
| Final prompt with empty/whitespace `voicePrompt` | Engine drops it before detection; direct L2 input yields UNKNOWN | Remain silent; never infer a person or hangup from silence alone |
| `last:false` partial transcript | Engine and tracker ignore it for final classification | Wait for final replacement; do not send digits/ring on a partial |
| Final music artifacts, filler words, repeated fragments | L2 known artifact rules yield UNKNOWN without a model call | No owner ring; unknown arbitrary junk still requires safe L1 handling |
| Recognized repeated queue announcement | Hold/queue rules and bounded cache classify hold_message | Stay silent; do not restart agent turns for every repetition |
| Menu or callback offer | IVR, not HUMAN | Follow the current menu and approved task only |
| Novel named-agent + organization + offer of help after hold | New deterministic HUMAN/high fast path | Begin the approved owner handoff |

Synthetic markers `[music]`, `[instrumental music]` and `♪ ♫` in the fixtures
are simulations, **not claimed literal Twilio events**. Final junk text is still
untrusted. Lyrics or a recording can sound like a conversational response.
The detector cannot prove liveness from identical text; an unseen recording of
an exact agent introduction can fool the fast path. The requested prompt form
is recognized immediately after hold, without a name/company lookup. Known
repeated introductions, machine/queue language and partials do not take that
path. Ambiguous greetings remain UNKNOWN and need contextual confirmation.

## Menu guidance and callback scope

Use the supplied task-guidance file verbatim as the starting task. It instructs
Hermes to seek reservations/change-a-flight assistance without inventing missing
identifiers. DTMF is for an explicitly announced press-key option; speech is for
an explicitly spoken/open-ended option. Use native Relay `sendDigits` through
`hermes_line_call_press_digits`, once per finalized prompt. Do not hard-code
Delta's digit map: this lane did not observe its current IVR.

For a missing confirmation or SkyMiles number: choose an announced skip/no-number
option. At a speech-enabled prompt, state that the number is unavailable and
request a representative. Try “agent” once, then “representative” on the next
retry. If a mandatory identifier loop offers no supported escape, stop after
two unsuccessful retries; an invented identifier is never a fallback.

“Press 1 to get a call back” is an offer, not permission. This task declines it
using the advertised stay-on-line/decline option, speech if supported, or silence
when that is explicitly the default. Never press 1 by association with an older
menu. If a future task explicitly permits callback, its approval must cover the
callback number and resulting change from hold/bridge; this trial does not.

## Expected phases and timestamps

Use monotonic time for durations; retain corresponding UTC events for correlation.
Record unavailable measurements as unavailable, not zero. No audio recording.
Mask identifiers, codes and endpoint data; preserve stable call/turn IDs privately.

| Phase | Record | Pass behavior |
|---|---|---|
| Approval and preflight | Build, fixture hashes, approved task/roles, fresh balance and reserved budget, global deadline | Both legs authorized; guards and shutdown verified in dry rehearsal |
| Dial and disclose | Create/answer, connected Relay setup, disclosure sent | One business call; recording off; clear AI disclosure |
| Menu | First menu prompt, each final prompt ID, speech or digit action, queue entry | Reach queue without made-up identifiers or callback acceptance; no duplicate digits |
| Hold | Queue entry, each recognized announcement/artifact, reconnect/heartbeat health, first human transcript | Same business leg; zero false HUMAN and zero false owner rings; no unsolicited hold chatter |
| Detect | Receive time of first usable final agent transcript, detector verdict, HUMAN_DETECTED transition | At most 2 s from usable final transcript receipt to human transition for the target greeting |
| Ring and bridge | Ring request, owner answer, private acceptance, conference join for both legs, first two-way speech | Business stays alive; owner explicitly accepts; private information remains private |
| End | Hangup request, all terminal legs/conference, balance/usage settlement | All legs stop; no orphan or new retry; truthful result |

Definitions:
- Menu time = first menu receipt → queue entry. Target ≤5 minutes and at most
  two unsuccessful retries at the same mandatory-information step.
- Hold time = queue entry → first usable final human transcript. Include silence
  and repeated announcements; separately note reconnect gaps.
- Detection latency = final transcript receipt → HUMAN_DETECTED. Also report
  classifier-only wall time. Acoustic onset → transcript is a separate live
  STT measurement if an authorized observer can mark it; do not infer it from
  text timestamps or count local replay latency as end-to-end latency.
- False-human count = HUMAN verdicts before genuine human arrival, independently
  annotated by the operator. Also count `human_assumed`, ring attempts and model
  calls during hold; a zero L2 count does not excuse a false engine transition.
- Bridge time = confirmed human → two-way owner/business connection. Break out
  ring time, answer→acceptance, and acceptance→both conference joins. Target
  acceptance→connected ≤5 s; target total ≤30 s when the available owner answers
  within the ring timeout. These are trial targets, not measured provider SLAs.

## Deadline and balance stop rules

Use a **45-minute total wall-clock cap from the first business create attempt**,
including menu, hold, owner ringing and bridge. This conservative cap can give
less than 45 minutes of hold; it never means 45 minutes of hold plus unlimited
menu/bridge time. Set business provider TimeLimit ≤2700 seconds and a matching
local watchdog. The owner leg receives only the remaining global lifetime.
Do not begin bridging when there is insufficient time for the configured owner
ring timeout plus cleanup; end rather than create an orphan near the deadline.

Account balance stop line: **USD 5.00**, matching the current trial harness.
A start balance merely above $5 is insufficient for a long hold. Before dialing,
reserve the worst-case call envelope, including Relay/business minutes, owner
leg and conference minutes, carrier rounding/taxes and shutdown lag. Require
`fresh_balance - reserved_envelope >= 5.00`. Also require project incurred spend
plus this envelope to fit the existing $15 operational ceiling and $20 absolute
brief cap, unless the coordinator supplies a newer approved budget.

The old S2 planning rate gives 45 × $0.084 = $3.78 for business+Relay alone;
it excludes owner/conference, inference, fees and tax and is **not a current
quote**. The authorized preflight must verify actual applicable rates and
remaining project budget; this offline lane did not retrieve them.

During the call, reserve/estimate running costs locally and check the balance
at an authorized bounded cadence (proposed 60 s). Stop all legs immediately if
actual or conservative projected balance would reach the $5 floor, if project
budget is exhausted, if rates/usage/balance are unavailable beyond one check
interval, or if the global deadline arrives. Include at least the polling and
shutdown exposure in the reservation; delayed provider billing is not permission
to exceed the budget. The current before/after-only harness does not implement
this ongoing guard: L6 must supply and test it before the live call.

Other stop conditions: two unsuccessful menu retries, menu time >5 minutes,
unapproved callback/transfer/payment/flight change request, false HUMAN or owner
ring during a recording, auth/session mismatch, unrecovered Relay loss, provider
refusal, owner decline/no-answer, business hangup, owner/operator stop, or orphan
leg. Cancel every pending owner ring and conference on abort. No automatic redial.

## Offline evidence and live acceptance

**83 L2 tests pass** (`tests/l2/round4-test-output.txt`). The new authored airline
corpus contains 12 training events (11 nonhuman/1 human) and 15 held-out events
(13 nonhuman/2 humans); held-out text was written before the rule extension.
Labels/context are explicit; only engine state and utterances reach the detector.
The airline matrices and measured local latency samples are in
`tests/l2/round4-airline-metrics.json`.

Both no-model and deliberately overconfident fake-model runs have **0 false
HUMAN and 0 missed HUMAN** on each new split, with zero model calls. Held-out
agent-introduction classifier time was below 1 ms on this machine. This is not
live STT or engine scheduling latency. Original 31 held-out nonhuman predictions
remain unchanged and existing hold/consent/destination suites pass.
A virtual 45-minute schedule tests 176 artifact/announcement events followed
by a human at minute 44, partial→final at 500 ms, and duplicate suppression.
No-event intervals cause no invocation. Memory bounds remain 12 recent turns,
128 phrase results, 256 replay IDs; there is no real-time 45-minute soak here.

Reproduce:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=plugin python3 -m unittest discover -s tests/l2 -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=plugin python3 tests/l2/test_airline_hold.py
```

A live PASS requires reaching a real representative, zero false human/ring
before that representative, the ≤2 s recognized-transcript detection target,
one approved owner ring, private acceptance and successful two-way bridge,
complete cleanup and spend within limits. Report all raw latency samples.
Reaching the cap without a representative is a safe timeout/inconclusive result,
not a successful hold-and-bridge trial. An owner no-answer is a safely handled
failure, not a bridge pass. Failed readiness checks mean **do not run**.
No claim of first-attempt or universal false-positive immunity is made from
these small synthetic sets. Coordinator must resolve the named L1/L6 blockers
and obtain the destination approval before any live trial.
