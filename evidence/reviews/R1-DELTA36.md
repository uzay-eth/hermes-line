# R1b delta 36 — L1 round 39, diff-only review

**Delta run 4 verdict: NO.** One blocking functional finding remains: after a segment's four questions are exhausted, intervening speech outside hold can prevent budget renewal indefinitely, even after a subsequent long hold. A real agent then cannot obtain the confirmation question and the owner is never rung. A narrower unsafe-offer wording gap is listed separately as backlog.

## Scope

Reviewed the requested `git diff 4f2dcd0..HEAD -- plugin/line/engine tests/l1 docs/lanes/L1.md` at main `1a76fc44a05c099740898be84cf4156127ec1120`, containing final L1 `3014fff`. The exact diff is saved as [reviewed.diff](reviewed.diff). Source inspection was limited to that diff and the immediate phrase-send guard context needed to understand the changed entry gate. This is not an L2 classifier audit or a new general engine/harness review.

The pre-existing untracked `evidence/main-tests/l1-after-r39.txt` was present at the initial and final status checks and was not changed or used as test evidence. The tracked source was exported into this scratch directory for execution.

## Per-defect result

| Defect | Fixed? | Evidence |
|---|---|---|
| D2: hold announcements labelled possible_new_party | **YES for the L1 containment requested here; no claim that the detector itself is fixed.** | The changed phrase path refuses non-assistant labels and the exhausted new-party path is silent. The independent replay deliberately preserves the bad labels for hold announcements; it produces no assistant phrases after entering hold. L2 implementation is outside this diff. |
| D3: exhausted questions and assistant phrases into hold prevent later agent handoff | **NO, partially fixed.** | The recorded Delta 3 sequence now reaches an appended minute-23 agent, but the intervening-speech variant below permanently prevents renewal after four questions. The unwanted post-budget assistant fallback is removed. |

## Blocking finding B1 — Medium severity, blocks under the explicit real-agent criterion

**A completed segment cannot recover after one outside-hold utterance, regardless of subsequent hold duration.**

The changed `_on_detection` records `last_unheld_speech_at` for a non-hold label while outside `on_hold`. `_new_hold_segment` at `machine.py:1102` requires both 120 seconds since the latest question **and** `last_unheld_speech_at <= last_question_sent`. Once the second comparison becomes false after the fourth question, time alone cannot make it true. Entering hold does not update the reference. `_question_budget` refuses the next question, so the only event that could move `last_question_sent` forward cannot occur.

Reproduction through public `CallSession.offer`, real engine decisions and owner-ring method, with scripted detector labels and fake provider placement:

| Synthetic time | Input / label | Result |
|---|---|---|
| 100, 125, 150, 175 | Agent-like greeting / unknown + possible_new_party | Four fixed questions sent at 101.25, 126.25, 151.25, 176.25 |
| 200 | `Bot prompt.` / automated_assistant, while outside hold | `last_unheld_speech_at=200`; one permitted pre-hold phrase |
| 210 | `Please hold.` / hold_message | Enters hold; phrases pause |
| 600 | Real-agent greeting / unknown + possible_new_party | No question, although 390 seconds of hold have elapsed |
| 603 | `Yes.` / human | Cannot confirm: no pending question; no owner placement |
| 610 | `Please hold.` / hold_message | Still hold |
| 1480, 1483 | Agent greeting, then `Yes.` | Still four total questions, no pending question and zero owner placements |

**Control:** omit only the outside-hold utterance at 200. The greeting at 600 gets a fifth question at 601.25; the answer at 603 confirms and rings once. The hard cap of twelve is not involved in the failed sequence.

The author unit test explicitly expects refusal when outside-hold speech occurred after the last question, even much later. That validates the implemented comparison but does not establish recovery after a new continuous hold period. The permanently exhausted segment contradicts the purpose of restoring confirmation opportunity after 120 seconds of hold.

**Correction direction:** track the start of the current eligible hold/silence interval, or otherwise allow a new 120-second interval after the latest outside-hold speech. An earlier non-hold utterance must delay renewal, not prevent it forever. Preserve the 20-second spacing and twelve-per-call cap. Add the paired intervening-speech/control sequence through actual sends, not just manual counter assignments.

This is a blocking finding under the coordinator's explicit criterion even though its severity is Medium: it stops a real agent from being confirmed and leaves the owner un-rung.

## Backlog, not blocking

**M1 — Medium: explicit channel-switch wording is not always silent.** `assistant.py`'s new `_CHANNEL_OFFER` catches the recorded messaging and say-yes/no forms, but not `Would you like to switch channels?`. With an `automated_assistant` label, that input emits `Representative.`. The independent case uses the real phrase chooser and output path.

This violates the requested channel-offer silence rule, but the observed output neither accepts the offer nor leaks data, commits to an action, rings, or blocks later agent confirmation. Add direct switch-channel wording and equivalent variants to the fail-closed offer handling. The tested `send you a message`, recorded message-to-phone offer and `Or you can say, no.` controls are silent.

## Independent probes

[fixtures/probes.py](fixtures/probes.py) contains thirteen observations: eleven pass, B1 and M1 fail. [CASES.md](CASES.md) records complete output/state/event evidence; [probes.log](probes.log) is the raw JSON output. Exit 1 deliberately signals the two conformance failures.

- **Delta 3 replay:** all 31 recorded business entries, preserving their relative times, through the worst-case detector from the added test. The engine uses quiet=1.2. An agent greeting is appended at minute 23, followed by a clear yes. Result: nine questions total, no assistant phrase after hold, one owner placement after the appended answer. The only assistant phrase is Representative before hold.
- **Bot says yes during hold:** exact `Yes.` labelled hold_message confirms only with a pending question and a later start stamp. This causes one owner placement, as the previously accepted bot-says-yes residual permits; no bridge or transaction is performed by the probe. The existing owner acceptance requirement is not changed by this diff.
- **No pending question:** hold-labelled `Yes.` does not ring.
- **Promotional yes:** `Yes, enjoy our special offer.` and `Say yes to receive a message.` do not confirm despite a pending question.
- **Pre-question partial:** its final `Yes.` retains the earlier start and cannot confirm, even when labelled hold_message. The setup places the partial after the greeting but before the quiet-delayed question is sent.
- **Actual budget sends:** three eligible segments produce four questions each, at least twenty seconds apart; a thirteenth attempt is refused. This complements the author's manually assigned-counter unit test. The configured hard cap is intentional and is not itself raised as a defect.
- **Renewal:** the paired B1 sequence isolates outside-hold speech as the cause of permanent refusal.
- **Unsafe offers:** three wording controls stay silent; explicit channel switching exposes M1.

The controlled-clock replay drains the quiet gate after each input. It is a transcript/engine simulation, not acoustic STT or provider timing evidence.

## Author tests and execution boundary

**All six requested author tests passed in 138.446 seconds (exit 0)**, including the real-detector and worst-case replays. These passing tests do not cover the intervening-speech recovery failure above.

The requested `tests/l1/test_r39_hold_queue.py` ran using the inherited in-memory ASGI transport: no listening socket, HTTP network connection or WebSocket network connection is created. Real ingress and engine paths remain active; storage and providers use the author harness fakes. The real-detector replay uses the local detector with the fake model boundary. The original FastClock multiplier and engine constants are unchanged. See [author.log](author.log) and [author-result.txt](author-result.txt) for the exact result.

Reproduction from this directory:

```text
/usr/bin/python3 runner.py author probes
```

The runner reports each child exit independently. It denies all networking, including loopback, and real `~/.hermes-line/` reads. HOME/TMPDIR/HERMES_HOME are scratch paths; bytecode is disabled; hermes is a nonfunctional stub. Test writes remain in scratch fixtures and `/dev/null`; source export, reports and logs remain under delta-36 scratch. The author replay is allowed 240 seconds wall time and 120 CPU seconds; no provider or model service is accessed.

No network, calls, provider access, protected-folder reads, repository writes, commits, pushes or installations occurred. The NO verdict is for this single review round and does not authorize a fourth Delta call.
