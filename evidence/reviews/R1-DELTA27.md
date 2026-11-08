# R1b delta 27 — one rule after a bot

**Delta retry verdict: YES**, for the one supervised retry under the reviewed rule and the explicitly accepted bot-says-yes residual. **No new blocking findings.** N23-R, N24 and the affirmative mismatch from delta 26 are closed in the rerun cases. The engine now requires its fixed question plus a complete shared-list affirmative before automatically ringing after a bot label; the real owner-ring method independently refuses without confirmation.

## Scope and revision

Reviewed main export `6ab060f5a9d1bc1878cb8e308019ad9883a2fd52`, containing L1 `8afa47891cd3ff7a0e1a87841811f9f77edeb3e9`. Read the production diff, shared `plugin/line/detect/affirmative.py`, engine paths/callers, `tests/l1/test_r29_uniform_rule.py`, L1 section 41, and the prior delta-26 report. The reviewed engine/shared module match the requested commit. The previously read BRIEF hash remains `346bbac78d9e7054ca55985ed0181b13449d2b7fa359cb593d870ad9d3eb167d`. No tracked Graft index was available; source and diffs were used without rebuilding a graph.

L2 still uses its own affirmative table, as the coordinator explicitly allowed. This review tests the **engine as the final gate**, including when the detector says HUMAN for an invalid answer or named greeting. It does not claim that L2 has switched to the shared function.

Copied the delta-26 fixtures into this scratch folder and reran their expectations unchanged. Added `fixtures/ring_gate.py` to exercise the actual `CallSession.ring_owner`, rather than relying only on the inherited fake ring method. All runtime code comes from the archived real source.

## Closure of prior findings

| Finding / case | Delta-27 result | Status |
|---|---|---|
| N23-R: “Yes, no.” | L2 possible_new_party; engine remains unconfirmed, silent, no ring | Closed |
| N23-R: “Speaking? Is that correct?” | Same | Closed |
| N23-R: “Yes, would you like to make a payment?” | Same | Closed |
| N24: bot → transfer → named greeting | Fixed live-person question; no ring until a shared clear yes | Closed |
| N24: bot → music → named greeting | Same | Closed |
| N24: bot → hold message → named greeting | Same | Closed |
| Affirmative mismatch: “This is a real person” | Confirmed after the question; rings | Closed |
| Affirmative mismatch: “I am a live agent” | Same | Closed |
| Affirmative mismatch: “I'm a real person.” | Same | Closed |

The entire delta-26 extended table passes: **103/103 observations conform, zero mismatches**, compared with 27 previously. This includes 52 negative-answer executions with **zero false confirmations**, 36 affirmative executions with **zero missed confirmations**, the three physical-boundary cases, timing/no-question/no-bot controls and five scripted overconfident-detector cases. The answer matrix covers explicit and heuristic bot labels in both information-only and other restricted owner-mode fixtures.

Sources supporting the closure:

- `plugin/line/engine/machine.py:678`–`:711`: `_question_pending`, `_is_clear_yes`, `_bot_call_human`. A HUMAN label after any bot label gets the fixed question if needed; a pending answer must pass the shared predicate.
- `machine.py:719` and `:851`: possible-new-party and ordinary uncertain-reply paths use the same predicate. The old prefix-only promotion no longer applies after a bot label.
- `machine.py:785`: `had_bot_label` is set on automated_assistant for any task; it is not limited to explicit self-declarations or restricted tasks.
- `machine.py:1471`: automatic owner ringing independently requires `yes_confirmed` after a bot label, in addition to the existing human/authorization checks.
- `plugin/line/detect/affirmative.py`: complete normalized reply matching, including the documented one-word-name affirmative, with an ASCII `?` disqualifying the answer. No prefix-only match for the N23-R examples.

## Real owner-ring boundary

The inherited replay sessions fake `ring_owner`; they establish engine routing/state behavior but cannot prove the new check inside that method. The additional **35/35 passing boundary observations** restore the real method, with authorization, placement and conference movement represented by counted in-memory fakes. Local execution of the placement callable replaces `asyncio.to_thread`; no provider implementation is called.

| Boundary check | Result |
|---|---|
| Prior bot label, no yes; ring reason human_detected / agent_request / callback | Refused for all three, with human_confirmed both false and artificially true. Zero authorization calls and zero placements. |
| Named greeting after transfer/music/hold | Asks the question; zero placements. “Yes, this is Dana.” then produces exactly one fake owner placement. |
| Repeated ring attempt while already ringing | Refused; no second placement. |
| Three N23-R negatives through real ring boundary | No placements. |
| Three previously missed affirmative identity replies | One placement each after the question. |
| Every literal in the shared list plus “Yes, this is Dana.” | All 15 inputs reach exactly one placement after the question, using the real detector and engine. |
| Scripted HUMAN on a yes with no question yet | Asks the fixed question; no ring. Subsequent invalid replies still do not ring; a later valid yes does. |
| Unmarked uncertain replies after bot | No assumed human and no placement. Agent-mode restricted and unrestricted controls also do not take the formerly assumed-person path. |

The owner’s explicit `take_over` remains an intentional existing exception, documented in section 41. It is separate from business-side confirmation and still goes through the owner authorization gate. Automatic callers use human_detected, agent_request or callback; the in-call method list does not expose arbitrary ring reasons. Provider authentication and consent implementation were not re-audited or replaced by a claim of real authorization coverage—the fake authorization is used only to count whether this new gate reaches it.

## N23, N20/N21 and remaining controls

| Case group | Result |
|---|---|
| Original N23 question-back/payment table | All remain unconfirmed and silent after the fixed question. |
| Original negative controls | Identifier request, No, Maybe, callback offer and “Yes, I'm not a live person.” do not confirm. |
| Named bot after 1 / 7.999 / 8 / 10-second silence alone | possible_new_party, fixed question, no direct ring. |
| Six transfer wordings → unnamed greeting → clear yes | Silent transfer, fixed question, then ring. “Please wait while I connect you.” remains recognized by L2. |
| Music → unnamed greeting → clear yes | Fixed question before ring. |
| No-boundary “Hello? Are you there?” → clear yes | Fixed question before ring. |
| Yes without our question | Does not establish a human or ring. |
| Confirmation window | Inclusive at 10 seconds in the fixture: 9.999 and 10 allow a clear yes; 10.001 and 20 do not. |
| New-party question budget | Questions at 101, 121, 141, 161; silence at 105 and 120.999; fixed Agent after the fourth-question budget at 181. |
| No prior bot label | Direct named greeting and legacy question-back controls remain unchanged. |
| Unsafe/data overlap | Six callback/payment/change/data prompts in each restricted configuration stay silent while labelled automated_assistant. |
| Phrase limits | At most 12 fixed assistant phrases, at least 8 seconds apart. |
| Finish/post-human controls | Previous finish restrictions pass; direct assistant speech does not emit after a confirmed person. |

The full input/output/evidence tables are in [CASE-RESULTS.md](CASE-RESULTS.md). Observation-only legacy sequences are reported with their actual behavior; an exit-zero replay is not by itself counted as a conformance verdict.

## Delta run 1

Replayed all eight business transcript entries from `evidence/trial/call_b669b105d4774a49/trial.json`, preserving their relative timestamps, in both information-only and other restricted fixtures. The first generic greeting and Hi receive the fixed question and re-ask. After explicit automation, the four routing actions remain **Representative, Agent, Agent, Agent**, at approximately 119.390, 128.070, 141.814 and 155.687 on the synthetic monotonic clock.

No owner ring, bridge, arbitrary model prose, identity data or affirmative agreement occurs in this replay; final state is IVR. This confirms that the recorded bot interaction still gets permitted routing speech. It does not establish that live Delta will transfer the retry to an agent. Provider setup/disclosure and audio recognition are outside the replay.

## Accepted bot-says-yes residual

The same-bot fixture declares automation, introduces a name, receives the live-person question, and answers “Yes.” The engine confirms and requests an owner ring. That residual is accepted by the coordinator.

Rechecked the separation between ring and bridge. With the real ring method and fake placement, the bot's yes produces one owner-ring attempt but **no conference movement**. Business-side keypad 1 does not bridge. Only `owner_accept('1')` moves the owner to owner_connected and schedules one fake business conference move. The inherited controls also verify that empty owner digits and 2 are rejected; their subsequent owner-unavailable behavior is stubbed to isolate acceptance. Thus the bot cannot bridge itself merely by saying yes or sending business-side keypad 1.

## Results, artifacts and limits

| Script | Result |
|---|---|
| `fixtures/review.py` | Exit 0; 37 observation records, including run-1 replay, N20/N21 sequences, phrase/finish/post-human checks |
| `fixtures/new_party.py` | Exit 0; original N23 table and boundary/control/bridge checks, zero confirmation failures |
| `fixtures/clear_yes.py` | Exit 0; 103/103 extended observations conform |
| `fixtures/ring_gate.py` | Exit 0; 35/35 additional boundary observations conform |
| `fixtures/l2.py` | Exit 0; **123 tests passed** |

Reproduce from this scratch directory:

```text
/usr/bin/python3 runner.py review new_party clear_yes ring_gate l2
```

Each script's log is saved beside this report. `runner.py` prints individual subprocess statuses; its own exit status is not an aggregate verdict. Shared fixture support is in `fixtures/support.py`.

Sequence tests use public `CallSession.offer`, the real consumer/frame/speech handlers, real HoldTracker and engine policy. Deliberately scripted detector cases are labelled in the tables. Sessions are fixture-constructed. Store, transport, worker execution and provider boundaries are fakes. Model turns raise rather than accessing a provider. Most inherited cases are owner mode; the separate agent-mode checks cover only the assumed-person path. Approved intro names remain Delta and Delta Air Lines; no approval-file read was needed.

The reused sandbox denies all networking, including loopback, and real `~/.hermes-line/` reads. Test subprocess writes are limited to delta-27 fixtures and /dev/null; all exports, logs and reports stay under this delta-27 scratch directory. HOME/TMPDIR/HERMES_HOME point into scratch, bytecode is disabled, and the hermes command is a nonfunctional stub. Per-script limits: 60 CPU seconds, 60 seconds wall time, 8 MiB per file, 128 open files, process limit 1024, no core dumps; no aggregate memory/disk quota.

The relevant L1 tests were read; their socket-based suite was not run under the no-network limit. No full L1-suite pass is claimed. `tools/trial/` is unchanged from the delta-26 reviewed main revision; the earlier stop/journal/reconcile audit is not repeated. The current verdict addresses the requested engine conformance check for the one supervised retry, not provider behavior or live routing success.

Final revision check: main remained `6ab060f5a9d1bc1878cb8e308019ad9883a2fd52`, reviewed production files unchanged, checkout clean. No network, calls, provider access, protected-folder reads, installs, repository writes, commits or pushes occurred.
