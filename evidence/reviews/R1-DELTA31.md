# R1b delta 31 — provisional partials and global question counter

**Delta run 3 verdict: NO.** The original N30 correction cases and N31 counting cases pass. One confirmation bypass remains: a final correctly marked as predating the live-person question can ring the owner if its detection result arrives after the reply timeout. The immediate-result control does not ring.

## Scope and revision

Reviewed main `b38d1649227bc5234ecf6d9dfa448a5f9fd8f244`, containing L1 `e7728087262cdc480281ce086e072221cd81f643`, against delta 30. Read the production changes, provisional/question/late-detection paths, round-33 and updated round-32 tests, and section 45. Main and reviewed files remained unchanged at the final check; checkout clean. The previously read BRIEF retains its specified hash. No tracked Graft index was available; source/diffs were used without rebuilding.

Copied the prior scratch fixtures and reran the adapted delta-28/29/30 probes, both Delta transcripts and the detector suite. Added `fixtures/r33.py` for scope, budget and asynchronous final-handling checks. Tests use public `CallSession.offer`, the real frame consumer, detector, engine and owner-ring method. Storage, transport, authorization and provider placement are counted in-memory fakes. Controlled monotonic clocks drive real asyncio timers; the separate wall-clock check uses actual elapsed time. No provider is invoked.

The author's scope is applied as specified: promotion is outbound/pre-human recovery, and the global question limit applies to strict calls. The Delta fixtures are strict information-only calls with callback/payment/change restrictions.

## N30-R — Medium — a timeout loses the final's “predates question” protection

Sources in the reviewed tree:

- `plugin/line/engine/machine.py:474`: final-after-promotion handling marks the frame `predates_question=True`.
- `machine.py:568`: `_handle_frame` copies that marker into session state and clears it in `finally`.
- `_handle_speech`: on a detector timeout, schedules `_late_detection` and returns.
- `machine.py:913`: `_late_detection` forwards the result and text, but not the frame's temporal/provisional metadata.
- `_question_pending` and `_bot_call_human`: the late confirmation decision reads the now-cleared session flag.

### Reproduction

The final below belongs to an utterance that began **before** Hermes asked its question. The engine itself identifies that fact; the finding does not depend on guessing which utterance the final belongs to.

| Synthetic time | Input/action | Result |
|---|---|---|
| 100 | Partial `Yes`, no earlier question | No confirmation |
| 102.51 | Promotion deadline serviced | Provisional recovery only; question scheduled |
| 103.8 | Quiet completes | Fixed live-person question sent; count 1 |
| 104 | Final `Yes`, completing that partial | `final_after_promoted_partial`, identical=true, **predates_question=true** |
| 106.6 | Detector still delayed past the configured 2.5-second reply budget | detect_slow; frame handler returns and clears predates_question |
| 106.6 onward | Delayed real detector is released | HUMAN/high arrives with late=true; engine confirms |
| 108 | Pending work drained | Connecting line sent; **one fake owner placement; ringing_owner** |

The delay is inserted at the local worker boundary. After release it calls the **actual HoldTracker**, not a scripted HUMAN classifier. The 2.5-second reply budget matches the live EngineConfig default. No real model/provider latency was induced.

**Control:** the same partial/question/final sequence with immediate real detection leaves `human_confirmed=False`, `yes_confirmed=False`, sends no connecting line and makes zero placements. Its HUMAN verdict is correctly rejected while predates_question remains true.

The failed run logs both the correct marker and the later acceptance:

```text
final_after_promoted_partial: identical=true, predates_question=true
detect_slow
detect: human/high, late=true
live_person_confirmed_by_reply
state: human_detected → ringing_owner
```

Impact: a pre-question utterance is accepted as the answer to a question it could not have answered. This crosses the specific confirmation gate introduced by the fix. It is distinct from the accepted residual of a bot actually replying with a complete yes after the question. No automatic bridge, payment or data disclosure occurs; the owner-key safeguard remains.

**Required correction:** preserve per-utterance metadata through the asynchronous detection/late-result path. A late verdict must use the original frame's temporal status rather than a session flag cleared when the consumer moves on. Carrying immutable metadata into the decision is preferable to leaving a global flag set across unrelated frames. Add the paired immediate/delayed real-detector regression.

## Prior findings and scoping checks

| Probe | Result |
|---|---|
| Delta-30 partial Yes followed by explicit bot denial, expanded question, payment question or Yesterday | Now unconfirmed; no owner placement; corrected finals are processed. |
| Non-prefix correction after promoted Yes | Now no premature confirmation or ring. |
| Promoted open-question prefix followed by payment final | No assistant phrase from the provisional fragment; final unsafe-offer silence applies. |
| Promoted Yes by itself while a question is pending | Does not confirm or ring. |
| Final Yes to a question sent **before** that partial | Valid final confirms and rings once; tested separately from the pre-question case. |
| Original stopped-partial recovery | One fixed question after promotion plus quiet. |
| Original timeout/late-HUMAN duplicate-question race | One question, joined slot, count 1, no ring. |
| Old four-attempt stimulus | Actual sends and the global question counter now agree. |
| Strict call, six attempts spaced 25 seconds apart | Four actual questions and count 4; subsequent attempts silent; all sent intervals ≥20 seconds. |
| Unrestricted/no-bot direct question requests | Six requests send six questions; strict-call cap is not incorrectly imposed. This isolates the scheduling API, not a full unrestricted conversation. |
| Inbound partial-only input | No promotion. |
| Outbound partial-only input after entering ringing_owner | No promotion. |
| N25 connecting/fallback timing | Ordinary quiet-period probes pass; independent wall-clock question/connecting gaps are approximately 1.25/1.20 seconds. |
| N27 assistant spacing | Prior delayed-phrase regression remains fixed. |
| N28 fallback | Nonempty replies remain `unverified`, silence `unanswered`, with neutral closing and quoted summary. |

The current global counter is updated in the send path, and strict-call spacing/budget are checked before scheduling and again before sending. The old alias reads the same count. The original N31 mismatch is closed in the reruns.

Two previously documented limits remain unchanged: continuous partial-only traffic with no 2.5-second inactivity gap does not promote, so the 21-second continuous-stream observation remains silent; the quiet gate's documented eight/ten-second caps can permit speech with less than 1.2 seconds of quiet. Neither is presented as a newly introduced finding here. The requested asynchronous confirmation check independently blocks approval.

## Delta replays and retained controls

Both recorded business transcripts were replayed with recorded relative timing and quiet=1.2:

- **Delta run 1:** all eight business entries; fixed question, Representative, Agent twice; final IVR, no owner placement or bridge. The global strict-call budget now suppresses the old immediate re-ask, so the resulting dialogue and detector context differ from earlier rounds.
- **Delta run 2:** all nine business entries; Representative, fixed question, Baggage question, Agent; final IVR, no owner placement or bridge.

Every output in those timed replays follows the ordinary 1.2-second quiet interval. No arbitrary model prose, identity information or fallback-to-bot was observed. These are transcript simulations, not acoustic/STT tests or evidence of a successful future live transfer.

The complete-final confirmation matrix still passes **103/103** observations and the inherited owner-ring/boundary suite **35/35**. Those regressions set quiet=0 to isolate confirmation policy; timing claims come from the separate timing probes. The accepted bot-says-yes residual still needs owner keypad 1 to bridge. A real agent saying “Yes, how can I help?” still fails the exact shared list, with the previously documented risk of delayed/missed handoff.

The actual trial config factory retains EngineConfig quiet=1.2; the missing-attribute default of zero applies to bare fixture objects. No live CLI or credentials path was executed.

## Results, artifacts and reproduction

- `r33.log`: seven observations; six pass and the delayed pre-question-final case fails. Exit **1** deliberately reports the failure.
- `promotion.log`: all 13 asserted delta-30 comparisons/controls pass; the continuous-partial case is observation-only. Exit 0.
- `r31.log`: all 17 adapted timing/config/fallback observations pass. Exit 0.
- `wall_quiet.log`: real-wall-clock timing check passes.
- `review.log`, `new_party.log`, `clear_yes.log`, `ring_gate.log`: inherited final-transcript, quantitative and owner-key controls pass under their stated fixture settings.
- `l2.log`: **130 tests passed**.
- [CASE-RESULTS.md](CASE-RESULTS.md): complete inputs, times, states, outputs and event evidence.

Reproduce from this scratch directory:

```text
/usr/bin/python3 runner.py r33 promotion r31 wall_quiet l2 review new_party clear_yes ring_gate
```

The runner prints individual subprocess exits; its own status is not an aggregate verdict. Copied `quiet.py` supplies helpers, not the active expectations. Relevant L1 tests were read, but no full socket-based L1/L6 suite was run, and no full L1 pass is claimed.

The reused sandbox denies all networking, including loopback, and real `~/.hermes-line/` reads. Test writes stay in delta-31 fixtures and /dev/null; all exports/logs/reports stay under delta-31 scratch. HOME/TMPDIR/HERMES_HOME are scratch paths, bytecode is disabled, and hermes is a nonfunctional stub. Per-script limits: 60 CPU seconds/60 seconds wall time, 8 MiB per file, 128 open files, process limit 1024, no core dumps; no aggregate memory/disk quota. No approval-file read was needed.

No network, calls, provider access, protected-folder reads, installations, repository writes, commits or pushes occurred. The NO verdict does not authorize a third Delta call or imply a new owner go.
