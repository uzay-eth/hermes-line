# R1b delta 37 — round 40 recheck

**Delta run 4 verdict: NO.** The original B1 deadlock and M1 switch-channel case are fixed. One new blocking functional issue remains in segment renewal: time spent speaking can be counted as silence, allowing recordings to consume extra segments and exhaust the twelve-question cap before a real agent arrives.

## Scope and per-finding result

Reviewed only `git diff 4e2fa69..dc7c173 -- plugin/line/engine tests/l1 docs/lanes/L1.md`, at main `dc7c173b2fe2e8aa835472521898e7b9a914a957`, containing L1 `c3e6fb2`. The requested diff is retained as [reviewed.diff](reviewed.diff). L2 rounds 11–13 were used by the current-main real-detector replay; their implementation and the other reviewer's verdict were not independently re-audited.

| Prior finding | Fixed? | Evidence |
|---|---|---|
| B1: outside-hold speech permanently prevents segment renewal | **Yes, original case closed.** | Unmodified delta-36 `fixtures/probes.py` now passes both intervening-speech and control sequences. Four questions, bot prompt at 200, hold at 210, agent at 600: a fifth question is sent and the fresh yes rings once. |
| M1: `Would you like to switch channels?` emits Representative | **Yes.** | The same prior output-path case is now silent. The new author wording table covers channel/chat/text/app and continuation variants. Section 52 calls this B17; it is the same prior M1 case. |

All thirteen inherited probe observations pass on current main, including the Delta 3 worst-case replay, exact yes on a hold label, pre-question partial rejection, promotional yes rejection and actual twelve-question cap. These prior fixtures were copied unchanged.

## Blocking B2 — Medium severity; blocks under the real-agent-never-confirmed criterion

**Segment renewal counts an utterance's duration as a period without business speech.** Two related changes cause this:

- `_on_detection` now stores the **start stamp**, rather than arrival/decision time, in `unheld_marks` for outside-hold finals.
- `_new_hold_segment(now, utt)` removes marks at or after the current utterance's start, then compares the prior reference against **now**, rather than establishing that the required interval occurred before the current utterance began.

It is correct to avoid treating a newly arrived greeting as proof that the preceding silence did not happen. It is not correct to count a long greeting/recording itself as part of that silence. Likewise, the start of previous long speech does not identify when that speech ended.

### Reproduction: recordings earn two extra segments, then the real agent cannot confirm

The public engine ingress receives continuous partial updates, one per simulated second, followed by a real final. The detector is deliberately worst-case: recording finals are `unknown` with `possible_new_party`; the final clear yes is HUMAN. State remains outside hold. No fabricated start stamp or private budget mutation is used.

| Time | Input | Observed result |
|---|---|---|
| 100, 125, 150, 175 | Short recorded greetings | Four questions, last sent at 176.25 |
| 200–309 | Continuous partials of a recorded announcement | Business activity throughout; no 120-second silence or hold interval |
| 310 | Final of that announcement, start stamp 200 | Current mark excluded; `now - last_question` exceeds 120; **fifth question at 311.25** |
| 335, 360, 385 | More short recorded greetings | Questions six through eight |
| 410–519 | Continuous partials of another recorded announcement | Again no 120-second silence or hold interval |
| 520 | Final, start stamp 410 | **Ninth question at 521.25** |
| 545, 570, 595 | More short recordings | Questions ten through twelve |
| 1000 | Actual agent greeting | No question: hard per-call budget exhausted |
| 1003 | `Yes.` | No pending question, no confirmation, zero owner placements |

The twelve-question cap itself is respected. The error is granting the additional segments without the required qualifying hold/silence, causing that cap to be reached prematurely. Once reached, more hold or later agent greetings cannot restore confirmation opportunity on that call. This meets the coordinator's explicit blocking criterion even though the issue is Medium rather than Critical/High.

### Shorter corroborating case: previous speech start mistaken for its end

After four questions, a bot utterance starts at 200 and sends partials continuously through 309, finalizing at 310 with an `automated_assistant` label. It does not itself ask a live-person question. A possible-new-party recording at 335 nevertheless gets question five at 336.25: renewal measures from the previous **start** at 200, although only 25 seconds have passed since that speech finalized. This demonstrates that merely changing the current-utterance exclusion would not fully address the timing error.

### Controls

- Repeated short recording finals every 25 seconds through time 600 stay at four questions and make no owner placement.
- A recording after genuine long silence gets the allowed fifth question; `Yes, enjoy our special offer.` does not confirm or ring.
- The inherited pre-question partial/final and exact post-question yes checks still pass. No direct bypass of the clear-yes/stamp confirmation checks was observed; the demonstrated consequence is premature budget exhaustion.

These are adversarial transcript-delivery sequences, not a claim that Delta will deliver 110-second finalization delays. The continuous partials explicitly distinguish ongoing speech from silence, and the engine already observes them for quiet gating. The requested review specifically asks whether a recording can earn extra questions through this new exclusion rule; these cases show that it can.

**Correction direction:** evaluate the eligible hold/silence interval that existed before the current utterance began, while separately preserving the end/latest activity of prior outside-hold speech. Keep the immutable start stamp for confirmation, but do not use it as the end of speech for renewal. Carry that eligibility through the send guard/reset without allowing processing or utterance duration to create a false interval. Add the two long-utterance cases alongside the genuine-silence control.

## Backlog

No additional independent Medium/Low finding is asserted in this round. Prior M1 is closed. The known exact bot-says-yes residual remains: a whole clear yes after the pending question can ring the owner, while owner acceptance is still required to bridge. Promotional text containing yes and a final whose partial began before the question remain unconfirmed. This round does not change that previously accepted residual.

## Tests and evidence

**Both requested author suites pass:** round 39, 6 tests; round 40, 7 tests. All 13 author tests pass alongside the 13 inherited probes. The two new failing observations are the related B2 timing cases above. They are absent from the author suites.

Final verification found the same main revision and unchanged scoped source; the exported machine hash matches current main.

- [probes.log](probes.log): all thirteen unmodified delta-36 observations pass; exit 0. The replay includes all 31 recorded Delta 3 business entries, worst-case hold labels and an appended minute-23 agent. The engine asks and rings on that recorded sequence; no assistant phrase is sent after hold.
- [exclusion.log](exclusion.log): four new observations; two controls pass and two related cases expose B2. Exit 1 deliberately reports conformance failure.
- [CASES.md](CASES.md): complete independent inputs/outputs, state, counts and event observations.
- [fixtures/exclusion.py](fixtures/exclusion.py): reproductions through real engine ingress, detection dispatch, question scheduler and owner-ring method, with local external-boundary fakes and controlled monotonic time. Quiet remains 1.2 seconds.
- [author39.log](author39.log), [author40.log](author40.log): requested author suites; see their result files for subprocess status.

Reproduce from this scratch directory:

```text
/usr/bin/python3 runner.py probes exclusion author39 author40
```

The runner prints each child exit; its own status is not an aggregate verdict. Author tests retain their original 20× FastClock and constants. The inherited in-memory Starlette ASGI transport replaces network sockets while retaining production ingress and service/engine paths. Author provider/model boundaries are local fakes. The added exclusion probes are transcript/logic tests, not acoustic or provider validation.

No full L1/L2/harness audit is claimed. Test subprocesses deny networking, including loopback, and real `~/.hermes-line/` reads. Scratch HOME/TMPDIR/HERMES_HOME, disabled bytecode and a nonfunctional hermes stub are retained. Writes are restricted to scratch fixtures and `/dev/null`; source export, diff, reports and logs remain under delta-37 scratch. The author subprocess wall limit is 240 seconds and CPU limit 120 seconds.

The pre-existing untracked `evidence/main-tests/l1-after-r40.txt` was not read or modified. No network, calls, provider access, protected-folder reads, repository writes, commits, pushes or installations occurred. This review does not authorize a fourth Delta call.
