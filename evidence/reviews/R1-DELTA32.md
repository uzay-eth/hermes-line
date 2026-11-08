# R1b delta 32 — utterance start stamp

**Delta run 3 verdict: NO.** The delta-31 late-result finding is fixed for an utterance whose original stamp is retained. One medium-severity finding remains: promotion can lose that original stamp before the real final arrives. A pre-question utterance can consequently confirm a person and ring the owner.

## Revision and method

Reviewed `main` at `34526c4c3e3b780fb6155632add36546d8fb19f2`, containing L1 `f71f800deb2703e95fcab78b6e7f969e6cd320e4`, against `evidence/reviews/R1-DELTA31.md`. The reviewed engine and round-34 test have no changes between that commit and the reviewed head. Read the production diff, stamp acquisition/propagation and owner-ring guards, the round-34 tests and L1 section 46. Main stayed at that head and the repository stayed clean. BRIEF hash remains `346bbac78d9e7054ca55985ed0181b13449d2b7fa359cb593d870ad9d3eb167d`. No tracked Graft context index was available; source and diffs were used without building one.

Exported the tracked source into this scratch directory. Reused delta-31 fixtures, which include the adapted delta-28/29/30 probes. In `fixtures/r33.py`, removed only the assertion reading the deleted session attribute `predates_question`; the frame-completion assertion and outcome checks remain. Added `fixtures/r34.py` for stamp acquisition, revisions and repeated promotion. The recorded cases use public `CallSession.offer`, the real frame consumer, real detector, engine decision paths and actual owner-ring method. Transport, storage, authorization and provider placement are local fakes. The delayed-detector case delays the worker boundary and then executes the real detector. No scripted HUMAN verdict is needed for the new finding.

Controlled monotonic time drives the asynchronous probes; an independent wall-clock probe checks ordinary quiet timing. Delta fixtures are outbound strict information-only calls, with callback/payment/change restrictions and approved names `Delta` / `Delta Air Lines`. No approval-file read was needed.

## N32 — Medium — promotion ends stamp tracking before the real utterance ends

The frozen `SpokenUtterance` preserves a stamp correctly once constructed. The remaining error is how subsequent frames obtain that stamp:

- `plugin/line/engine/machine.py:513`: every final clears `utt_first_partial_at`, including an internally synthesized promotion.
- `machine.py:574`: promotion replaces `self.promoted` with the latest partial's text/time/start and submits a synthetic final through `offer`.
- `machine.py:515` and `machine.py:546`: the real final inherits the promoted start only when its normalized text equals or extends the stored partial and arrives within ten seconds of promotion.
- `machine.py:506`: a subsequent nonfinal update, after promotion cleared the first-partial field, starts tracking at that later update. A second promotion overwrites the old start.
- `machine.py:817`, `machine.py:1167` and `machine.py:1763`: confirmation and the owner-ring boundary faithfully compare the resulting stamp, but that stamp can now be later than the actual beginning of the utterance.

### Reproduction without any text revision or ten-second expiry

One business utterance produces two partial updates and then its real final. No intervening real final or explicit new-utterance boundary is supplied.

| Synthetic time | Input/action | Observed result |
|---|---|---|
| 100 | Partial `Yes`, `last=false` | First-partial start is 100 |
| 102.51 | Promotion deadline serviced | Provisional utterance starts at 100; synthetic final clears first-partial tracking |
| 103.8 | Quiet gate completes | `Am I speaking with a live person?` sent; question count 1 |
| 104 | Another partial `Yes`, `last=false`, from the same unfinished utterance | Engine starts tracking again at 104 |
| 106.51 | Second promotion serviced | Provisional utterance starts at 104; replaces the promoted record |
| 107 | Real final `Yes.` | Immutable final utterance is incorrectly stamped 104, after the question |
| 108.3 | Pending speech completes | Connecting line sent; **human_confirmed=true, yes_confirmed=true, ringing_owner, one fake owner placement** |

Captured `_on_speech` stamps are `100 provisional`, `104 provisional`, `104 final`. The original start is 100, and the question was sent at 103.8. The two partials are only four seconds apart, so the documented ten-second abandonment rule does not explain the reset. The final is processed normally; the ring guard accepts the wrong start time.

### Additional cases and controls

For the first five rows below, the original partial arrives at 100, is promoted at 102.51, and causes the fixed question to be sent at 103.8. The final at 104 is the finalization of that partial, not a separate spoken answer.

| Case | Final stamp / result | Conforms |
|---|---|---|
| Partial `Yes` → final `Yes.` at 104 | Stamp 100; no confirmation or placement | Yes |
| Partial `Yeah` revised to final `Yes.` at 104 | Stamp 104; one placement | **No** |
| Partial `I am` revised to final `I'm a real person.` at 104 | Stamp 104; one placement | **No** |
| Revised `Yeah` → `Yes.` with real detector delayed beyond 2.5-second reply budget | Stamp 104; timeout confirmation; one placement | **No** |
| Identical `Yes.` final delayed until 113 | Stamp 113 after promoted record's ten-second matching window; one placement while the 15-second confirmation window is still open | **No** |
| Second promotion of the same unfinished `Yes` utterance | Stamp advances from 100 to 104; one placement | **No** |
| Revision `I am` → `I'm a real person.` at 100.5, before promotion | Stamp stays 100; asks a question, does not ring | Yes |
| Completed greeting, question sent at 101.3, genuinely new final-only `Yes.` at 102 | Stamp 102; confirms and rings once | Yes |

The delayed-identical case is consistent with the author's documented ten-second text-matching limit. That limit nevertheless cannot prove that a late final belongs to a newly started utterance. It is supporting evidence; the repeated-promotion case fails without reaching either ten-second limit.

These are deliberately constructed transcript delivery sequences, not claims that Delta run 1 or 2 exhibited these STT patterns. They demonstrate that the stated rule is not enforced for all allowed partial/final sequences. In particular, textual revision or another provisional promotion does not establish that a person began a new utterance after the question.

**Impact:** an utterance spoken before Hermes asked can satisfy the required response gate and cause an owner ring. This is different from the accepted residual of a bot actually answering yes after the question. No automatic bridge, payment or data disclosure was observed; owner keypad acceptance remains required.

**Required correction:** preserve the earliest start of the unfinished provider utterance across synthetic promotions and revised finals. A synthetic final must not act as the real utterance's completion or reset its identity. Do not infer a new confirming utterance solely from non-prefix wording or expiry of a text-matching timer. Where the protocol cannot establish whether a final is new, fail closed for confirmation and obtain a fresh answer. Add the repeated-promotion, revised-final and delayed-final cases alongside genuine-new-reply controls.

## Previous finding and regression results

| Coverage | Result |
|---|---|
| Delta-31 N30-R: identical final of pre-question partial, immediate real detector | No confirmation or owner placement |
| Delta-31 N30-R: same final, detector delayed past reply timeout and then released | Original start 100 survives the late verdict; no confirmation or owner placement. **Original finding closed.** |
| Valid partial/final answer to a question sent before the partial | One valid owner placement |
| Promotion scope | No promotion on inbound calls or after reaching `ringing_owner` |
| Strict global question budget | Six attempts 25 seconds apart produce four actual questions; counter agrees |
| Unrestricted direct question scheduling | Six direct requests remain uncapped, as scoped by the author |
| Delta-30 corrected finals: bot denial, expanded natural answer, payment wording, Yesterday, non-prefix bot correction | No premature confirmation; finals processed; provisional text never independently rings |
| Promoted open-question prefix followed by payment final | No category phrase derived from the provisional fragment |
| Delta-29 stopped partial recovery | One fixed question after promotion and quiet |
| Timeout/late-HUMAN duplicate-question race | One scheduled question and one count |
| Prior quiet, assistant spacing and fallback probes | Pass adapted expectations; neutral fallback, nonempty response `unverified`, silence `unanswered` |
| Live trial config factory | Real `EngineConfig` path retains quiet=1.2; bare-object missing-attribute fallback remains zero |
| Independent wall-clock quiet probe | Question and connecting line gaps approximately 1.25 / 1.25 seconds |
| Shared clear-yes matrix | 103 observations pass |
| Owner-ring/boundary controls | 35 observations pass, including owner keypad 1 before bridging |
| L2 suite | 130 tests pass |

All seven adapted `r33.py` observations pass. The new `r34.py` matrix has eight observations: three controls pass and five variants expose N32. Its exit 1 is intentional.

The earlier documented limits remain: continuous partial-only traffic without a 2.5-second inactivity gap does not promote; the 21-second continuous-stream fixture is observation-only. Ordinary quiet checks pass, but the documented bounded-wait caps are not an unconditional 1.2-second silence guarantee. These unchanged limitations are not additional new findings here.

## Delta runs and residuals

Replayed every recorded business transcript entry at its recorded relative timing through the real engine with quiet=1.2:

- **Run 1**, `call_b669b105d4774a49`: eight business entries; fixed live-person question, Representative, Agent twice. Ends in IVR, unconfirmed, zero owner placements.
- **Run 2**, `call_2243b7f205194ab5`: nine business entries; Representative, fixed live-person question, Baggage question, Agent. Ends in IVR, unconfirmed, zero owner placements.

Every replay output follows at least 1.2 seconds of ordinary quiet. Neither replay emits arbitrary model prose or enters the human fallback. These are transcript simulations, not an acoustic/STT validation or a prediction of successful live handoff.

The accepted bot-says-yes residual still requires the owner to press 1. The cost of the narrow clear-yes list also remains: a real agent answering `Yes, how can I help?` fails closed and may be missed or delayed. Neither residual excuses accepting an utterance that started before the question.

## Artifacts and reproduction

- [CASE-RESULTS.md](CASE-RESULTS.md): complete recorded case inputs, outputs, stamps where captured, states and event evidence.
- [fixtures/r34.py](fixtures/r34.py): new eight-case stamp matrix.
- [r34.log](r34.log): five failed variants and three passing controls.
- [r33.log](r33.log): seven passing adapted delta-31 checks.
- [promotion.log](promotion.log): thirteen passing delta-30 checks and one continuous-stream observation.
- [r31.log](r31.log): seventeen passing timing/config/fallback observations, including both Delta replays.
- [run-results.txt](run-results.txt): individual subprocess exit codes and log tails. All scripts except r34 exit 0.

Reproduce from this scratch directory:

```text
/usr/bin/python3 runner.py r34 r33 promotion r31 wall_quiet l2 review new_party clear_yes ring_gate
```

The runner's own status is not an aggregate verdict; inspect each recorded subprocess exit. Copied `quiet.py` supplies helpers; its historical failing expectations are superseded by the adapted active probes. The new socket-based round-34 tests were read, not executed: networking, including loopback, was prohibited. No full L1 or L6 suite pass is claimed.

The test sandbox denies networking and real `~/.hermes-line/` reads, allows writes only within scratch fixtures and `/dev/null`, redirects HOME/TMPDIR/HERMES_HOME, disables bytecode and uses a nonfunctional hermes stub. Exports, scripts, logs and reports are confined to delta-32 scratch. Per-script limits are 60 CPU seconds/60 wall seconds, 8 MiB per file, 128 open files, process limit 1024 and no core dumps; there is no aggregate memory/disk quota.

No network, calls, provider access, protected-folder reads, installations, repository writes, commits or pushes occurred. This review does not authorize Delta run 3 or replace the required new owner go.
