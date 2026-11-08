# R1b delta 33 — partial promotion removed

**Delta run 3 verdict: NO.** Partial promotion is removed and all inherited delta-32 through delta-28 regressions pass. One medium-severity residual remains: an update after ten seconds without partials moves an unfinished utterance's pending start later, allowing a pre-question utterance to confirm and ring the owner.

## Scope and method

Reviewed `main` at `79419bfd4ac55e162cea76af7812bd33680a7087`, containing L1 `260848bde01bf3da1dd64f31910c40eb8a434cd1`, against `evidence/reviews/R1-DELTA32.md`. Read the production diff, current ingress/stall/question/confirmation paths, `tests/l1/test_r35_no_promotion.py` and L1 section 47. The reviewed engine, round-35 test and lane document have no subsequent changes between the requested commit and reviewed head. Final revision and status checks found the same head and a clean repository. The exported engine's SHA-256 matches the live source. The previously read BRIEF retains its specified hash. No tracked Graft index was available; no graph build was run.

Exported source and copied prior fixtures into delta-33 scratch. The only adaptation needed for the delta-32 fixture was removing its access to the deleted `SpokenUtterance.provisional` field. Its input sequences, start-time observations and outcome assertions are retained. Earlier adapted delta-31/30/29/28 probes ran unchanged. Historical case names mentioning promotion now exercise the equivalent elapsed time with promotion absent.

Added `fixtures/r35.py`, using public `CallSession.offer`, the real frame consumer, detector, confirmation logic and owner-ring method, with local storage, transport, authorization and provider fakes. Spies capture constructed utterances and business detector inputs without changing decisions. Strict outbound fixtures use quiet=1.2, reply window=15, callback/payment/change restrictions and names `Delta` / `Delta Air Lines`. These timing values match `EngineConfig` defaults. No model or provider is called. Simulated monotonic time drives asynchronous timers; a separate actual-wall-clock probe checks quiet timing.

## N32-R — Medium — a ten-second partial gap still replaces the earliest pending start

At `plugin/line/engine/machine.py:502`, a nonfinal prompt overwrites `utt_first_partial_at` whenever the interval since the previous partial exceeds `UTT_ABANDONED_S` (10 seconds). No real final is required. The eventual final then receives the later time at lines 506–509. The immutable utterance and downstream confirmation/ring checks preserve and accept that incorrect later stamp.

This is an explicit author assumption, not an accidental disagreement with the author's tests: section 47 and `test_N32_control_a_new_utterance_after_the_gap_starts_anew` treat a new partial after the gap as a new utterance. But a gap in partial delivery alone does not establish that the previous provider utterance finished or that a fresh spoken answer began. It conflicts with the coordinator's requirement to preserve the earliest pending partial and never move the start later. The accepted exception for asking the fixed question to a known bot does not authorize this timestamp reset.

### Reproduction

Model one unfinished provider utterance, with a delayed partial update and then its final. There is no intervening real final or explicit new-utterance boundary.

| Synthetic time | Input/action | Observed result |
|---|---|---|
| 100 | Partial `Yes`, `last=false` | Earliest pending partial = 100; no detection |
| 102.51 | Stall deadline serviced | Fixed `Am I speaking with a live person?` sent; count 1; no business utterance constructed |
| 110.01 | Another partial `Yes`, `last=false` | Pending start **replaced with 110.01** because the partial gap exceeds ten seconds |
| 110.11 | Real final `Yes.` | `SpokenUtterance.started_at=110.01`; real detector labels HUMAN/high; confirmation accepted |
| 111.41 | Quiet gate completes | Connecting line; `human_confirmed=true`, `yes_confirmed=true`, **ringing_owner, one fake owner placement** |

The question was sent at 102.51, after the original start of 100. The final arrives inside the configured 15-second response window. No fabricated metadata, scripted classifier or private confirmation call is used.

| Interval between the two partial updates | Final start stamp | Owner placements | Result |
|---|---:|---:|---|
| 9.99 seconds | 100 | 0 | Pass |
| 10.00 seconds | 100 | 0 | Pass |
| 10.01 seconds | 110.01 | 1 | **Fail** |
| 11.00 seconds | 111 | 1 | **Fail** |

A separate genuine-new-reply control first supplies the real final of a greeting, waits for the question, then sends a new partial/final `Yes.`. It correctly stamps the new reply at 102 and rings once. The distinction is the completed earlier utterance, not elapsed time alone.

These are adversarial transcript delivery sequences, not evidence that Delta's recorded calls exhibited such delays. The report does not infer how frequently this happens. It demonstrates that the rule is not enforced for a delayed update of an unfinished utterance. The author's gap test covers the interpretation “new speech after a gap”; it does not cover the equally representable interpretation “late update of old speech.”

**Impact:** an utterance that began before the live-person question can be accepted as its answer and ring the owner. This differs from the accepted residual of a bot actually replying yes after the question. Owner keypad acceptance is still required to bridge; no automatic bridge, payment or data disclosure was observed.

**Required correction:** do not replace a nonempty pending start solely because a partial interval elapsed. Preserve the earliest start until a real completion or a reliable provider utterance boundary establishes a new utterance. If identity is ambiguous, fail closed for confirmation and require a fresh answer after resolving that ambiguity. Add the paired 10.00/10.01-second update cases, alongside the valid new-reply control. A timer can bound recovery scheduling without redefining the start of a confirming utterance.

## What the fix closes

The eight delta-32 observations all pass, including its five formerly failing variants: revised `Yeah` → `Yes.`, revised `I am` → `I'm a real person.`, delayed real detection of a revised final, delayed identical final without a new partial, and repeated updates within the old promotion schedule. Their finals retain the original start and do not ring. The valid new-final control still rings.

The former promotion machinery and provisional utterance path are deleted. `_handle_frame` returns for nonfinal prompts. `_stall_watch` has no path that forwards partial text to detection or constructs a `SpokenUtterance`; it invokes the fixed-question scheduler directly.

The new round-35 matrix records 17 observations: 15 pass, and the two above-ten-second update cases fail.

| New round-35 check | Result |
|---|---|
| Partials containing `Yes.`, a bot introduction, callback offer, payment offer, named greeting, or baggage menu | All six pass: zero business utterances, zero business detector calls, zero business transcript entries, no confirmation/placement/assistant phrase; one fixed question |
| More partial updates of the same pending utterance within ten seconds | No second question |
| Known bot followed by stalled confirmation-number partial | Only the permitted fixed live-person question is added; no partial-derived phrase or detection |
| Business interrupt just before stall recovery | Question waits until at least 1.2 seconds of quiet |
| Six stalls separated by 25 seconds | Four actual fixed questions; counter agrees; sent intervals meet the 20-second limit; no business detection |
| Inbound or already ringing-owner scope | No stall question or partial detection |
| Final arrives before the stall deadline | Only that final becomes an utterance; stamp retains first partial; no stall action |
| Explicit real final, followed by a new answer's partial/final | New answer legitimately confirms and rings |
| Same unfinished utterance updated at 9.99/10 seconds | Original stamp retained; no ring |
| Same unfinished utterance updated at 10.01/11 seconds | N32-R, above |

The fixture initially treated the transcript as if it should be entirely empty; that assertion was corrected to check **business** transcript entries. Recording Hermes's own fixed question is expected. The final evidence contains only the two genuine failing outcomes.

## Earlier probes and Delta replays

| Artifact / coverage | Result |
|---|---|
| `r34.log`: delta-32 matrix | 8 observations pass |
| `r33.log`: delta-31 immediate/late detector, valid reply, scope and global-counter checks | 7 pass; original late-result finding remains closed |
| `promotion.log`: adapted delta-30 corrected finals, payment/unsafe-output and valid controls | 13 asserted checks pass; one continuous-stream observation |
| `r31.log`: adapted delta-29/28 quiet, recovery, duplicate-question, spacing, fallback and config checks | 17 pass, including both timed Delta replays |
| `wall_quiet.log` | Question/connecting gaps approximately 1.203 / 1.200 seconds |
| `clear_yes.log` | 103 observations pass |
| `ring_gate.log` | 35 observations pass, including owner keypad acceptance |
| `review.log`, `new_party.log` | Earlier complete-final bot/person/transfer, unsafe-offer, spacing and finish checks complete successfully |
| `l2.log` | 130 tests pass |

Both recorded business transcripts were replayed through the real engine with their recorded relative timing and quiet=1.2:

- **Run 1**, `call_b669b105d4774a49`: all eight business entries; fixed live-person question, Representative, Agent twice. Final IVR, unconfirmed, zero owner placements.
- **Run 2**, `call_2243b7f205194ab5`: all nine business entries; Representative, fixed live-person question, Baggage question, Agent. Final IVR, unconfirmed, zero owner placements.

Replay outputs follow at least 1.2 seconds of ordinary quiet. These are transcript simulations, not acoustic/STT tests or evidence that a future live handoff will succeed.

The previously accepted bot-says-yes residual still requires owner key 1 before bridging. A real agent saying `Yes, how can I help?` still fails the narrow clear-yes list, with the known risk of a delayed or missed handoff. Neither accepted limitation covers the timestamp reset above.

Continuous partial traffic without 2.5 seconds of inactivity still produces no stall question; the 21-second continuous-stream case remains observation-only. The established bounded-wait quiet caps also remain; ordinary quiet probes do not establish an unconditional 1.2-second guarantee. These are not new findings in this review. The real trial config factory retains the EngineConfig quiet default of 1.2; the zero fallback applies to bare fixture objects.

## Artifacts, reproduction and limits

- [CASE-RESULTS.md](CASE-RESULTS.md): complete recorded case evidence.
- [fixtures/r35.py](fixtures/r35.py): new stall and start-boundary matrix.
- [r35.log](r35.log): 17 observations, two failed boundary cases; intentional exit 1.
- [run-results.txt](run-results.txt): per-script exit status and log tails; all inherited scripts exit 0.

Run from this scratch directory:

```text
/usr/bin/python3 runner.py r35 r34 r33 promotion r31 wall_quiet l2 review new_party clear_yes ring_gate
```

The runner does not aggregate child exit status. Inspect the recorded exits and conformance summaries. The copied `quiet.py` supplies helpers; active adapted probes replace its historical expectations. The socket-based round-35 tests were read but not executed because networking, including loopback, is prohibited. No full L1/L6 suite pass is claimed.

Sandboxed test processes deny all networking and real `~/.hermes-line/` reads. Writes are restricted to scratch fixtures and `/dev/null`; HOME/TMPDIR/HERMES_HOME point into scratch, bytecode is disabled, and the hermes executable is a nonfunctional stub. Source export, scripts, logs and reports remain under delta-33 scratch. Per-script limits remain 60 CPU/wall seconds, 8 MiB per file, 128 open files, process limit 1024 and no core dumps; no aggregate memory/disk quota.

No network, calls, provider access, protected-folder reads, repository writes, installations, commits or pushes occurred. No approval-file read was needed. The NO verdict does not authorize Delta run 3 or substitute for a new owner go.
