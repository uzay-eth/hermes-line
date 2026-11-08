# R1b delta 28 — Delta run 2 and quiet-before-speech

**Delta run 3 verdict: NO.** The false-human fixes pass the recorded replays and confirmation controls. However, the 1.2-second wait does not cover all spoken lines, and missing final transcripts can leave the call silent after consuming question slots that were never spoken. Two smaller regressions affect phrase spacing and fallback outcome reporting. A new owner go would still be needed for any future approved run; no call was attempted here.

## Scope and evidence

Reviewed main export `ba37eac927ecec90a5efdfdec2fcb5f716108f67`, containing L2 `fa40c3c29cd8e87af04507b654fe699e92401848` and L1 `ad53768021ded1ad66e1abdaa4cc6a3936174a61`. Read both changes, detector/shared affirmative code, engine timing/confirmation/fallback paths, ConversationRelay TwiML, the new tests, L2 round 9 and L1 section 42. The previously read BRIEF retains its specified SHA-256. No tracked Graft index was available; source/diffs were used without rebuilding.

Main advanced during review to `831358da1b358ec06bef850c3bc34e0487e8b0ea`; the reviewed engine, detector and TwiML remained identical to the export, and the checkout was clean at the check. The L6 dry-run change is excluded from the verdict as requested. No full L6 rerun or new provider-recovery audit is claimed.

Business frames enter the real `CallSession.offer` queue, frame/speech handlers, detector and engine. Fixtures replace transport/storage and provider boundaries. The added timing tests restore the real `ring_owner` method with counted in-memory authorization and placement fakes. Model calls are either absent or deliberately scripted; no provider is accessed.

`fixtures/quiet.py` drives the real asyncio timer loop with a controlled monotonic clock and **quiet_before_speak_s=1.2**, including the recorded transcript intervals. `fixtures/wall_quiet.py` independently reproduces the immediate connecting-line defect with actual wall-clock waits. Inherited regression scripts explicitly use quiet=0 to isolate confirmation-policy regressions; their passing results are not evidence of the new timing guarantee.

## N25 — Medium — connecting and fallback lines bypass the mandatory quiet wait

Sources: `plugin/line/engine/machine.py:975` (`_on_human`), `:1014` (`speak`), `:1632` (`_script_finish`). The new wait is invoked for `_ask_live_person` and assistant phrases, not centrally for all outgoing speech.

Real-wall-clock reproduction through public ingress, with real detector and owner-ring method:

| Input/action | Time since latest business activity when Hermes sends speech |
|---|---:|
| Named greeting → fixed live-person question | 1.2018 s — passes |
| Business answers “Yes.” → “Thank you. Please hold for just a moment while I connect Fixture owner.” | **0.0063 s — fails** |

A controlled-clock reproduction emits the connecting line at exactly the yes frame's timestamp, gap 0. This is not the opening disclosure and therefore is not covered by the permitted exception. It demonstrates failure to enforce the stated quiet interval, not an acoustic measurement of actual overlap.

The fallback deadline path also directly sends “Thank you for your time. Goodbye.”. In the probe, a partial arrives at 100.5 and the real `_script_finish` is invoked as the deadline handler at the same time; the closing is sent with **0 seconds of quiet**. The forced deadline call isolates this path; the fixture does not claim a full provider/owner-timeout integration run.

Impact: the new protection against interrupting a continuing business utterance is incomplete, including immediately after an accepted live-person answer. Correction: enforce quiet for all in-scope spoken lines, while retaining the opening-disclosure exception and stale/phase protections. The pre-human-only scope in the lane notes does not satisfy the coordinator's “any line except the opening disclosure” requirement.

## N26 — Medium — partial transcripts can strand pending speech and spend unsaid questions

Sources: `machine.py:485`–`:494` (partials update activity then return), `:726`–`:750` (question counter/reservation), `:906`–`:940` (20-second quiet wait and dropped question).

The behavior depends on whether a final transcript ever started a decision:

| Probe | Actual result |
|---|---|
| One final generic greeting, followed by partials through 100.9, then silence | Question speaks at 102.11, 1.21 s after the last partial, even though those partials never finalize. This control passes. |
| Only partials at 100, 100.3, 100.6, followed by quiet through 160 | **No decision, no question, no output; counter remains 0.** No timer turns the abandoned partial into a recoverable event. |
| Final named greeting at 100; partial activity every 0.5 s for over 20 s; then quiet through 125 | **Pending question expires and is discarded.** Counter is 1, last_confirm is unset, zero spoken questions. Nothing retries when quiet returns. |
| Four final named greetings at 100, 122, 144, 166, each followed by over 20 s of nonfinal partial activity | **Counter advances to 4 with zero spoken questions.** A fresh greeting at 191, followed by quiet to 195, logs budget exhausted and remains silent. |

The answer to the requested budget probe is therefore: **yes, the 20-second/four-question budget advances for scheduled attempts, even if none is spoken; partial-only traffic by itself advances neither detection nor that budget.** Unlike assistant phrase slots, canceled/expired question reservations are not released. `last_confirm` is set only when speech actually occurs, so the engine correctly does not falsely claim a question was asked—but may have no question allowance left to ask one.

“Silent forever” here means no recovery on the remaining quiet call path without a new qualifying event, not an unlimited live call. Production's existing maximum-duration watchdog eventually ends the call; the fixture intentionally does not start that watchdog. After four lost slots, even a fresh named greeting failed to recover the probe.

Correction: add bounded recovery for abandoned partials/pending questions and account separately for pending reservations versus questions actually sent. Preserve both the quiet requirement and the four-question/20-second limits without exhausting the usable budget on unsaid lines.

## N27 — Low — assistant spacing uses reservation time, not actual speech time

Sources: `machine.py:751`–`:797` (`_assistant_reply`, `_phrase_when_quiet`).

A phrase reserved at 100 is delayed by partials until it speaks at **108.71**. A new assistant utterance at 110 reserves the next phrase because ten seconds have elapsed since the first reservation. It speaks at **111.21**. Both individually follow 1.21 seconds of business quiet, but the spoken phrases are only **2.5 seconds apart**, violating the existing eight-second assistant-spacing rule.

The twelve-phrase control and the old zero-delay spacing cases still pass. This regression is specific to the new delay. Correction: enforce spacing against actual sends as well as pending reservations, without allowing delayed tasks to bunch together.

## N28 — Low — incidental numbers/currency words make non-answers “answered”

Sources: `plugin/line/engine/fallback.py:103` (`looks_answered`), `machine.py:1632` (`_script_finish`) and task-outcome handling at `:2027`.

The question in these probes is “What is the fee for the first checked bag?”:

| Business reply | Observed fallback result |
|---|---|
| You're welcome. What can no problem. | unanswered — passes |
| Empty reply | unanswered — passes |
| It depends on your fare. | unanswered — passes the conservative rule |
| The first checked bag is 35 dollars. | answered (unverified) — passes |
| **I cannot quote the fee in dollars.** | **answered (unverified) — non-answer misreported** |
| **Please wait 2 minutes.** | **answered (unverified) — non-answer misreported** |

All closings are correctly neutral, and all summaries quote the reply and say “answer not verified.” However, the digit-or-currency heuristic treats an explicit refusal and a waiting instruction as an answer. The task-status code maps that result to DONE. In the fixture, `_script_finish` and its summary/result logic are real; final hang-up/storage completion is faked, so the persisted DONE mapping is a source-review conclusion rather than a tested database write.

The lane documents this heuristic, but it does not fulfill the broader requested property that a non-answer is `unanswered`. A conservative correction should avoid promoting unrelated numbers or a mere mention of currency into a completed answer. This is an outcome-quality issue, not a payment authorization or data-disclosure finding.

## Passing replay and confirmation checks

Both recorded calls were replayed using all business transcript entries and their recorded relative timestamps, then allowing pending quiet timers to complete. Original Hermes/provider actions were not replayed as new outgoing actions.

| Replay | Business entries | New output | Owner ring / final state |
|---|---:|---|---|
| Delta run 1, call_b669b105d4774a49 | 8 | Fixed question and re-ask; Representative; Agent three times | None / IVR |
| Delta run 2, call_2243b7f205194ab5 | 9 | Representative; fixed live-person question; Baggage question; Agent | None / IVR |

Every emitted replay line followed at least 1.2 seconds of recorded business quiet in the controlled clock. No human confirmation, arbitrary model prose, identity disclosure, fallback-to-bot or bridge occurred. These are transcript simulations, not audio/STT measurements or evidence that Delta will route a live third run successfully.

Other passing evidence:

- **130 L2 tests pass.** The truncated “I'm Del” and brand-scoped questions get local nonhuman handling; the run-2 detector fixture makes zero injected model calls. Adversarial model HUMAN outputs without a recognized name or qualifying shared yes are capped by the new detector path. L2 now imports the shared affirmative helper.
- A deliberately delayed HUMAN verdict on a restricted call with no bot history triggers/uses the fixed question and does **not** ring by itself.
- The inherited extended matrix remains **103/103 conforming**, with its former no-bot controls adapted to the newly required question/yes step. The separate boundary suite remains **35/35 passing**. These use quiet=0 and test confirmation rather than timing.
- The actual `ring_owner` guard still rejects automatic requests without yes_confirmed. Named greetings and model labels cannot substitute for the fixed-question/clear-yes step on restricted calls.
- ConversationRelay output contains `partialPrompts="true"`. No live provider behavior is claimed from the XML assertion.
- Original N23/N20/N21, shared-list negative/affirmative, owner-key, phrase-cap and finish controls pass under the stated fixture settings.

## Accepted residual and real-agent cost

**Bot says yes:** after the fixed question, a bot's literal “Yes.” confirms and requests the owner ring, as explicitly accepted. The real ring method is exercised with a fake placement counter. Business-side keypad 1 still does not bridge. The real owner-acceptance method requires owner keypad 1 to reach owner_connected and schedule one fake conference move. The missing quiet gap in the connecting speech remains N25; it does not bypass owner acceptance.

**Real agent says “Yes, how can I help?”:** tested after the fixed question with quiet=1.2. It fails the whole-reply shared list, stays unconfirmed, emits no connecting line and does not ring. This is the requested fail-closed result. The cost is a missed or delayed real-agent handoff: the person must eventually give an exact listed affirmative; an agent who keeps using natural expanded answers or hangs up may never reach the owner. It is not counted as a new finding.

## Artifacts, reproduction and limits

[CASE-RESULTS.md](CASE-RESULTS.md) expands the cases with input text, output timestamps, quiet gaps, labels, states, question counts and outcomes. Raw logs include detection and scheduling events.

```text
/usr/bin/python3 runner.py quiet wall_quiet review new_party clear_yes ring_gate l2
```

- `quiet.log`: 17 session observations plus the TwiML assertion; seven recorded failed checks, grouped into the findings above. Exit **1** deliberately reports the conformance failures.
- `wall_quiet.log`: independent real-time reproduction of N25; exit **1** deliberately reports the reproduced violation.
- `review.log`, `new_party.log`: inherited replays/sequence observations and quantitative assertions, exit 0.
- `clear_yes.log`: 103/103 observations conform, exit 0.
- `ring_gate.log`: 35/35 observations conform, exit 0.
- `l2.log`: 130 tests passed, exit 0.

The runner prints subprocess exits and saves logs; its own exit status is not an aggregate verdict. All scripts and shared fake support are under `fixtures/`. No full socket-based L1 or L6 suite was run, and the known L6 dry-run update is not a finding. The subsequent L6 change seen on main adds the question/yes step and telemetry; it was not executed here.

Tests run under the reused sandbox denying all networking, including loopback, and real `~/.hermes-line/` reads. Test writes are confined to delta-28 fixtures and /dev/null; all exports, logs and report writes remain within delta-28 scratch. HOME/TMPDIR/HERMES_HOME are redirected there, bytecode is disabled, and hermes is a nonfunctional stub. Per-script limits: 60 CPU seconds, 60 seconds wall time, 8 MiB per file, 128 open files, process limit 1024, no core dumps; no aggregate memory/disk quota. The long partial scenarios use virtual time rather than lengthy blocking sleeps. No approval-file read was needed.

No network, phone calls, provider access, protected-folder reads, installations, repository writes, commits or pushes occurred. The review's NO verdict is based on reproduced engine behavior, independent of the excluded L6 test.
