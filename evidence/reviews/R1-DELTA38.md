# R1b delta 38 — final B2 recheck

**Delta run 4 verdict: YES, within the stated supervised-test scope. B2 is fixed.** No blocking finding was confirmed under the coordinator's final-round threshold. The previously failing recordings no longer receive premature segment renewal, and the subsequent real agent can be confirmed.

## Scope

Reviewed only `git diff c9172dd..HEAD -- plugin/line/engine tests/l1 docs/lanes/L1.md` at main `2475112ce86de54fcd190f6b4393102135a81f32`, containing round-41 commit `6c0bc72`. The exact diff is retained as [reviewed.diff](reviewed.diff). This is the requested final recheck, not a renewed general engine, detector or harness audit.

The pre-existing untracked `evidence/main-tests/l1-after-r41.txt` was not read or modified. Tracked source was exported into this scratch directory. Tests use that export, not a patched production tree. Final verification found the same main revision and untracked-file status; the exported engine hash matches current main.

## B2 fixed: yes

The changed ingress code assigns the final's actual arrival to `_ended_at`, alongside the immutable `_started_at`. Both keys are engine-owned; incoming relay values are stripped and ingress recomputes them. `SpokenUtterance` carries both values through processing. The confirmation path continues to use the original start stamp.

The changed `_on_detection` records the **end** of outside-hold speech in `unheld_marks`. `_new_hold_segment` now measures the qualifying interval up to the **current utterance's start**, rather than decision/send time. For partial-only stall recovery, it uses the pending partial's start. Thus neither the current recording's duration nor the earlier recording's start is credited as silence. The question slot continues to carry the utterance into the send guard and reset.

The two original B2 cases pass without fixture edits:

| Reproduction | Previous result | Current result |
|---|---|---|
| Four questions, continuous partials 200–309/final 310, short recordings, continuous partials 410–519/final 520, more recordings | Twelve questions before the agent; later agent could not confirm | Still **four** before the agent. The agent at 1000 gets question five; `Yes.` at 1003 confirms and produces one fake owner placement |
| Four questions, bot speech 200–310, next recording at 335 | Fifth question despite only 25 seconds since speech ended | Still **four**; no renewal and no owner placement |

The first case now records end marks `[175, 310]` after the first long recording and `[385, 520]` after the second. Neither recording spends another question. These observations directly verify both halves of B2, not merely its final counter.

Controls also pass: dense short recordings remain at four; a greeting after genuine silence receives a fifth question; promotional wording containing yes does not confirm. The author round-41 control additionally verifies that a subsequent exact yes can confirm.

## Blocking findings

**None confirmed.** In the requested regressions and the realistic recorded hold-line replay, no path was demonstrated that leaks data or commits through a wrong owner ring, or permanently prevents a real agent from being confirmed. The original B1 recovery and M1 switch-channel silence fixes remain intact.

This conclusion is bounded by the diff, test inputs and faked external boundaries. It is not a claim that the detector can always distinguish a recording from a person, or that a future live call will transfer successfully.

## Backlog and accepted limits

No new independent Medium/Low finding is added in this round. The old B2 inputs were adversarial long-finalization timing cases, but they now conform; they are not left unresolved or used to block this verdict.

The unchanged limits remain:

- An exact clear yes after the pending question may come from a bot. The existing owner-key acceptance requirement still governs bridging; this accepted residual is not changed by round 41.
- A promotional sentence containing yes, or a final whose partial began before the question, cannot confirm in the retained probes. An agent using wording outside the exact clear-yes list can still be delayed or missed.
- The twelve-question hard cap can intentionally prevent further questions once legitimately spent. It remains the specified policy. The reproduced **premature** exhaustion from counting speech duration is fixed.
- An unresolved old partial can make the next final fail closed under the previously accepted start-stamp rule. Round 41 keeps that confirmation rule separate from renewal timing.

No additional speculative timing audit was started beyond this final-round scope.

## Retained probes and replay

`fixtures/probes.py` is byte-identical across delta 36, delta 37 and this review; its SHA-256 is `1de5a1f2d78c0e9c31f4f3cfbd6258f480c6efcdb1cc8ae02f757b00c9a34d02`. `fixtures/exclusion.py` is the unchanged delta-37 B2 fixture. All **17 observations pass**, with both subprocesses exiting 0.

| Coverage | Result |
|---|---|
| Delta 3: all 31 recorded business entries with worst-case hold labels, then agent at minute 23 | Nine questions total, one owner placement after the appended exact affirmative; no assistant phrase after hold |
| Exact yes labelled hold_message, no pending question | No confirmation or placement |
| Exact yes labelled hold_message, pending question and post-question start | Confirms; one placement |
| Promotional yes / say-yes message offer | No confirmation or placement |
| Final yes whose partial began before the question | No confirmation or placement |
| Original B1 with intervening outside-hold speech and paired control | Both renew, ask and ring on the valid reply |
| Actual sends across three segments | Four per segment, ≥20-second spacing, twelve total; thirteenth refused |
| Message, say-no and explicit switch-channel offers | Silent |
| B2 long-recording cases and silence controls | All four pass, as detailed above |

The independent probes use real public `CallSession.offer`, the frame consumer, question/confirmation logic and owner-ring method, with controlled monotonic time and quiet=1.2. Detector labels are deliberately scripted for the worst-case sequences. Provider placement, storage/transport boundaries and authorization are local fakes. The replay is transcript evidence, not acoustic/STT validation.

## Requested author tests

**All 17 requested author tests passed:** round 39, 6 tests; round 40, 7 tests; round 41, 4 tests. Every author-suite subprocess exited 0. Together with the 17 passing inherited probe observations, there are no failed checks in this round.

The three requested files run unchanged through the inherited in-memory ASGI transport:

- `tests/l1/test_r39_hold_queue.py` — includes worst-case and current-main real-detector Delta 3 replays, phrase gate, budget, hold-labelled yes and unsafe offers.
- `tests/l1/test_r40_segment_renewal.py` — original B1 recovery, genuine silence, renewal timing, spacing/cap and M1 variants.
- `tests/l1/test_r41_segment_end.py` — both B2 reproductions, genuine-silence/yes control and final-end/relay-stamp integrity.

Their original FastClock multiplier and engine constants are retained. See [author39.log](author39.log), [author40.log](author40.log), [author41.log](author41.log) and the corresponding result files for exact outcomes. The current detector is exercised by the author real-detector replay; no detector source review is claimed.

## Artifacts and reproduction

- [CASES.md](CASES.md): complete independent case evidence.
- [probes.log](probes.log), [exclusion.log](exclusion.log): raw results and event records.
- [probe-results.txt](probe-results.txt): inherited subprocess exits and summaries.
- `fixtures/`: copied probes, author-suite launchers and in-memory transport adapter.

Run from this scratch directory:

```text
/usr/bin/python3 runner.py probes exclusion author39 author40 author41
```

The runner reports each child exit separately; its own exit is not an aggregate verdict. The in-memory Starlette ASGI transport replaces the original harness's HTTP/WebSocket sockets while keeping production ingress and engine paths. Fake model/provider boundaries are local; no actual service is invoked. No full L1/L2/L6-suite pass is claimed beyond the requested files and probes.

Test subprocesses deny networking, including loopback, and real `~/.hermes-line/` reads. HOME/TMPDIR/HERMES_HOME point into scratch, bytecode is disabled and hermes is a nonfunctional stub. Test writes stay in scratch fixtures and `/dev/null`; source export, diff, scripts, logs and this report remain under delta-38 scratch. Author subprocesses have 240-second wall and 120-second CPU limits.

No network, calls, provider access, protected-folder reads, repository writes, commits, pushes or installations occurred. The YES is the engineering review verdict; it is not a new owner go or permission to place the call.
