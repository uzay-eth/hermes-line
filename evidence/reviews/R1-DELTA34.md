# R1b delta 34 — stamp without timers

**Delta run 3 verdict: YES, for the reviewed engine change and the stated supervised test.** N32-R is closed. No new blocking findings were found. This is the review decision; it does not place a call or substitute for the coordinator's required new owner go.

## Revision and scope

Reviewed `main` at `2aa6aba53f4be2244ed8b4dbcee2e7957cc53a9a`, containing L1 `198b8ff5fdf5bb8d5179a3d20956b5278dd63160`, against `evidence/reviews/R1-DELTA33.md`. Read the production change, `tests/l1/test_r36_stamp.py`, the adapted round-34/35 tests, L1 section 48, and the ingress, stall, asynchronous detection and owner-ring paths. There are no subsequent changes to the reviewed engine, round-36 test or section-48 document between the requested commit and reviewed head.

The repository remained clean at the same head. The scratch-exported engine and live source have matching SHA-256 hashes. The previously read BRIEF retains its specified hash. No tracked Graft index was available; no graph build was performed.

This is a targeted engine conformance review with the inherited trial/config/owner-acceptance regressions, not a fresh full harness or provider audit.

## Findings

**Open findings: none in the reviewed scope.**

**N32-R — previously Medium — closed.** In `plugin/line/engine/machine.py:501`, a partial sets `utt_first_partial_at` only when it is empty. No elapsed-time branch remains. The final obtains the pending start at lines 505–506 and clears it at line 507. A final with no pending partial uses its own arrival time. Searches of the current source found only those two assignments to the pending field: first-partial initialization and final clearing.

`utt_last_partial_at` now affects only the stall deadline. It cannot replace the start stamp. The stall watcher asks the fixed live-person question without constructing an utterance or sending partial text to detection. The immutable final utterance continues through normal, timeout and late-result paths, and the owner-ring boundary still compares its start with the latest question's send time.

The original boundary matrix now behaves consistently:

| Gap between updates of the same unfinished utterance | Original start | Final stamp | Owner placements | Result |
|---|---:|---:|---:|---|
| 9.99 seconds | 100 | 100 | 0 | Pass |
| 10.00 seconds | 100 | 100 | 0 | Pass |
| 10.01 seconds | 100 | 100 | 0 | Pass; formerly failed |
| 11.00 seconds | 100 | 100 | 0 | Pass; formerly failed |

The fixed question was sent at 102.51. All four finals therefore predate the question and cannot confirm. Both formerly failing cases use the actual detector and actual owner-ring method with local fakes at external boundaries.

## New controls and accepted cost

Added `fixtures/r36.py`; all eight observations pass:

| Case | Result |
|---|---|
| Abandoned pre-question partial, then two final `Yes.` replies | First retains start 100 and makes zero placements; second starts at 114 and rings once |
| Same sequence with a fresh partial update after eleven seconds | First still retains start 100; second final confirms |
| Each of those two sequences with the first final's real detector delayed beyond the 2.5-second reply budget | Timeout and late result retain the original stamp; first cannot confirm; next fresh final confirms |
| Unfinished utterance updated after 60 seconds | Pending and final stamp stay 100; no confirmation or owner placement |
| Unfinished utterance updated after 3,600 seconds | Same result; isolated timestamp/lifetime test, not a full-hour call simulation |
| Two finals with no pending partials | Independently stamped at their arrival times, 100 and 102 |
| Six stalled utterances, each completed by a real final, 25 seconds apart | Four actual fixed questions; count agrees; spacing ≥20 seconds; no owner placement |

The abandoned-partial result is the coordinator's accepted cost: a legitimate first answer may be rejected because it carries the earlier unresolved partial's start. In the tests, the next exact clear-yes final arrives while the existing reply window is still open and confirms. A later final is not guaranteed to ring after that window expires; the ordinary question and reply rules still apply.

One inherited expectation was adapted: six partial stalls separated only by time now remain **one** unfinished utterance and produce one question, not four. The old test depended on the removed abandonment timer. Its input sequence is unchanged; the new expectation also checks that the pending start remains 100. The separate completed-utterance case above preserves meaningful coverage of the global four-question budget.

## Delta-33 through delta-28 regressions

| Artifact / coverage | Result |
|---|---|
| `r35.log`: partial-only wording, known bot, quiet gate, scope, final cancellation and gap boundaries | 17 observations pass, including both original N32-R failures |
| `r34.log`: revisions, delayed finals, delayed real detection, repeated updates and valid controls | 8 pass |
| `r33.log`: original late-result case, valid reply, scope and global counter | 7 pass |
| `promotion.log`: corrected finals, unsafe-payment final, non-prefix correction and controls | 13 asserted checks pass; one observation-only continuous-stream case |
| `r31.log`: timing, recovery, duplicate-question race, assistant spacing, fallback, trial config and Delta replays | 17 pass |
| `wall_quiet.log` | Actual elapsed question/connecting gaps approximately 1.246 / 1.202 seconds |
| `clear_yes.log` | 103 observations pass |
| `ring_gate.log` | 35 observations pass, including owner keypad acceptance before bridging |
| `review.log`, `new_party.log` | Earlier complete-final bot/person/transfer, unsafe-offer, spacing and finish checks complete successfully |
| `l2.log` | 130 tests pass |

Partials containing yes, a bot introduction, callback/payment wording, a named greeting or a baggage menu create no business utterance, business detection or business transcript entry. They cannot confirm or ring. Stalled partials produce only the fixed question. Asking that question to a known bot remains within the accepted behavior and global budget.

The prior N30/N30-R confirmation failures, N31 counting mismatch and N32 promotion failures remain closed. Corrected finals still control safe/unsafe output. Promotion and provisional confirmation remain absent.

The real trial config factory retains `EngineConfig.quiet_before_speak_s=1.2`; the zero fallback applies only to objects without that field. Nonempty fallback responses remain `unverified`, silence remains `unanswered`, and closing stays neutral under the adapted prior probes.

## Delta runs and retained limitations

Replayed all recorded business entries at their recorded relative timing through the real engine with quiet=1.2:

| Replay | Outputs | Result |
|---|---|---|
| Run 1, `call_b669b105d4774a49`, eight business entries | Fixed live-person question; Representative; Agent twice | Final IVR, unconfirmed, zero owner placements |
| Run 2, `call_2243b7f205194ab5`, nine business entries | Representative; fixed live-person question; Baggage question; Agent | Final IVR, unconfirmed, zero owner placements |

Every output in those timed replays follows at least 1.2 seconds of ordinary quiet. These are transcript simulations, not acoustic/STT validation or a guarantee of successful live transfer.

The accepted bot-says-yes residual remains: a bot's exact affirmative after the question can cause an owner ring, but bridging still requires the owner to press 1. A real agent answering `Yes, how can I help?` still fails the narrow clear-yes list and may be delayed or missed. The new abandoned-partial behavior can also require another answer, as explicitly accepted above.

Continuous partial-only traffic without a 2.5-second inactivity gap still triggers no stall question; the inherited 21-second continuous-stream case is observation-only. Existing bounded-wait quiet caps remain, so ordinary quiet tests are not an unconditional 1.2-second guarantee. These unchanged, previously documented limits are not reopened as findings by this narrowly scoped change.

## Reproduction and review limits

- [CASE-RESULTS.md](CASE-RESULTS.md): complete recorded case observations, inputs, outputs, stamps where captured, states and events.
- [fixtures/r36.py](fixtures/r36.py): eight new controls.
- [r36.log](r36.log), [r35.log](r35.log): new controls and the original failing boundary matrix.
- [run-results.txt](run-results.txt): all individual subprocess exits are 0.

Run from this scratch directory:

```text
/usr/bin/python3 runner.py r36 r35 r34 r33 promotion r31 wall_quiet l2 review new_party clear_yes ring_gate
```

The runner reports individual child exits; its own exit is not an aggregate verdict. Copied `quiet.py` supplies helpers, while active adapted probes replace its historical expectations. Apart from the single no-final stall-count expectation described above, inherited delta-33 fixtures were reused without changes.

Tests use public `CallSession.offer`, the real frame consumer, detector, decision paths and owner-ring method. Storage, transport, authorization and provider placement are in-memory fakes. Controlled monotonic time drives asynchronous tests; the wall-clock probe uses actual elapsed time. No provider or model is invoked. The socket-based round-36 author tests were read, not executed, because networking including loopback is prohibited. No full L1 or L6 suite pass is claimed.

Test subprocesses deny all networking and real `~/.hermes-line/` reads. HOME/TMPDIR/HERMES_HOME point into scratch, bytecode is disabled, and hermes is a nonfunctional stub. Test writes are confined to scratch fixtures and `/dev/null`; source exports, scripts, logs and reports stay under delta-34 scratch. Per-script limits are 60 CPU/wall seconds, 8 MiB per file, 128 open files, process limit 1024 and no core dumps; no aggregate memory/disk quota.

No network, calls, provider access, protected-folder reads, repository writes, installations, commits or pushes occurred. No approval-file read was needed.
