# R1b delta 26 — clear yes only after a bot

**Delta retry verdict: NO.** The three original N23 inputs and the 10-second named-bot case now pass. However, L1 still rings on contradictory or question-shaped replies that L2 deliberately rejects, including “Yes, would you like to make a payment?”. Named greetings after transfer/music/hold also ring without asking the live-person question, contrary to this task's rule for calls that previously had a bot label. Owner keypad 1 remains necessary for bridging; the explicitly accepted bot-says-yes residual is not a finding.

## Reviewed revision and method

Reviewed a read-only export of main `ce02a208b638a99b9562acdb53821ab940ec0180`, containing L2 `30859cb50d71144ad45eae9d990c087735584a34` and L1 `7ad61a6702580bcae7568d4c3a0446e654bb0c8a`. Read the changed detector/engine sources, round-8 tests and lane notes, L1 round-28 tests/section 40, and `evidence/reviews/R1-DELTA25.md`. The previously read BRIEF retains its specified SHA-256. No tracked Graft index was available; source/diffs were used without rebuilding anything.

Copied the delta-25 runner and fixtures into this delta-26 folder. Updated only the old gap expectations: 1, 7.999, 8 and 10 seconds now all expect possible_new_party and a confirmation question. The original N23 negative expectations remain unchanged. Added `fixtures/clear_yes.py`, which runs L2's own round-8 negative/affirmative tables through the real engine, tests physical boundaries, and independently exercises L1's second layer with five scripted overconfident HUMAN results.

Except those five expressly scripted cases, business utterances enter **CallSession.offer → real consumer/frame/speech handling → real HoldTracker → real engine**. There is no direct invocation of removed recognition helpers or replacement of confirmation policy. Sessions are fixture-constructed; transport output, storage, worker scheduling, owner ringing and conference movement are fakes. Model-turn attempts raise. The information-only and other restricted fixtures both use owner mode, approved names Delta / Delta Air Lines, and the synthetic baggage category.

## N23-R — Medium — L1 promotes replies that L2 correctly rejects as nonaffirmative

Sources: `plugin/line/engine/machine.py:82`–`:92` (`_CLEAR_YES`, `_clear_affirmative`), `:690` (`_new_party_check`); `plugin/line/detect/__init__.py:165` (`_clear_affirmative`) and `:233` (post-assistant confirmation branch).

Use this one-second sequence through real frame ingress:

1. At 100: “I'm Delta's automated assistant.” → automated_assistant, “Representative.”
2. At 101: “Let me connect you to an agent.” → hold_message/transfer, silence.
3. At 102: “Hello, how can I help?” → unknown/possible_new_party; Hermes asks “Am I speaking with a live person?”
4. At 103, any of the following:

| Reply | L2 result | Engine result | Required result |
|---|---|---|---|
| Yes, no. | unknown + possible_new_party | **human_confirmed=True; ringing_owner** | Unconfirmed |
| Speaking? Is that correct? | unknown + possible_new_party | **human_confirmed=True; ringing_owner** | Unconfirmed |
| Yes, would you like to make a payment? | unknown + possible_new_party | **human_confirmed=True; ringing_owner** | Unconfirmed |

In all three, Hermes emits “Thank you. Please hold for just a moment while I connect Fixture owner.” These are not newly invented interpretations of affirmative wording: all three are already **NEGATIVE** cases in L2's `tests/l2/test_clear_confirmation.py`. They pass L2's unit tests because the detector correctly refuses HUMAN; the engine reintroduces the bypass.

Cause: L2 requires a complete affirmative identity answer and rejects questions and contradictions. L1 instead accepts a prefix matching yes/yeah/yep/yup/speaking unless its bot/negation blacklist matches. A plain “no” or a trailing question/payment offer does not trip that blacklist. Because the L2 result is unknown/possible_new_party, `_new_party_check` invokes this weaker predicate and directly calls `_on_human(confirmed=True)`. The new HUMAN-label guard does not repair this separate route.

Reproduced all three with both explicit automation and a heuristic bot label (“Are you calling about an existing booking?”), in both restricted fixture configurations: **12 false confirmations out of 52 negative-answer executions**. The original eight N23 negatives remain unconfirmed; those specific fixes work.

Impact: the new clear-affirmative requirement is bypassed, causing human-only fixed speech and an owner-ring request. No payment, data disclosure or automatic bridge was observed. This remains a blocking confirmation defect even though the owner must still accept a bridge.

Correction needed: make the end-to-end affirmative rule agree, including the unknown/possible_new_party path. Do not let L1's prefix test turn a detector-rejected contradiction, question or offer into confirmation. Run the negative table through real engine ingress as well as the detector.

## N24 — Medium — a physical boundary still permits owner ringing without any question or yes

Sources: `plugin/line/detect/__init__.py:238` and `:251`–`:255` (named greeting / physical boundary), `plugin/line/engine/machine.py:679` (`_new_party_question_pending`) and `:790`–`:796` (HUMAN handling).

| Sequence, one-second gaps | Actual result on the named greeting |
|---|---|
| Bot declaration → “Let me connect you to an agent.” → “Hello, this is Dana. How can I help?” | HUMAN; owner-ring request; no live-person question or affirmative answer |
| Bot declaration → `[music]` → same greeting | Same |
| Bot declaration → “Please hold.” → same greeting | Same |

L1's added check only applies when `new_party_questions > 0` and the reply window is open. Here no question has been asked, so it trusts L2's HUMAN label directly. L2 deliberately preserves named-HUMAN promotion following transfer, music or classified hold.

This behavior is intentional in the lane implementations: L1 section 40 and `test_N23_a_named_agent_without_a_pending_question_still_rings` preserve it, as does L2's named-transfer/music test. It nevertheless conflicts with this review's broader instruction: **“once a call had a bot label, only a clear yes to our live-person question rings the owner.”** A claimed transfer or a transcript music marker is not such an answer. A bot can also name itself after a transfer announcement.

Correction needed under the supplied rule: after any prior bot label, route even a named greeting after a physical boundary through the fixed confirmation question before ringing. The existing no-bot controls can retain their prior behavior. A deliberate coordinator exception for named physical-boundary greetings would change the acceptance rule; it would not close this discrepancy under the current wording. N23-R independently blocks the retry either way.

## Additional compatibility observation — Low — affirmative vocabularies disagree in the other direction

L2's affirmative table includes “This is a real person”, “I am a live agent” and “I'm a real person.” It returns HUMAN for each after the pending question. L1's prefix-only predicate rejects all three, logs `human_label_not_confirmed`, stays on_hold and says nothing. Reproduced in all four explicit/heuristic-bot × task configurations: **12 misses out of 36 L2-approved affirmative executions**.

The six other L2 affirmative variants pass, including plain Yes, Yes I am, Yeah, Speaking, “Yes, I'm a live person.” and “Yes, you are speaking to a live agent.” Thus literal clear-yes controls work. The three misses are a compatibility/liveness issue relative to L2's documented equivalent identity answers, not an unsafe agreement or a separate basis needed for the NO verdict. Aligning the affirmative contract should address both this mismatch and N23-R.

## Passing controls and replay

| Check | Observed result |
|---|---|
| Original N23 questions: “Hello, how can I help?”, “Is that correct?”, “Would you like to make a payment?” | No human confirmation, no ring, no speech after the fixed question. |
| Original controls: identifier request, No, Maybe, callback offer, “Yes, I'm not a live person.” | Remain unconfirmed. |
| Named bot after 1 / 7.999 / 8 / 10-second gap alone | unknown/possible_new_party, asks fixed question, no direct ring. Original 10-second N20 residual is fixed. |
| Six transfer wordings plus unnamed greeting → clear yes | Transfer is silent; greeting gets question; clear yes rings. Music and no-boundary “Are you there?” controls also work. |
| Clear yes without any live-person question | No confirmation or ring. |
| Confirmation-window boundary | Clear yes at 9.999 or 10 seconds after the question rings; at 10.001 or 20 seconds it does not. |
| New-party question budget with real detector | Questions at 101, 121, 141, 161; silence at 105 and 120.999; fixed Agent after budget at 181. No fifth question. |
| Independent L1 second layer, scripted HUMAN | Original three N23 questions and “Yes, I'm not a live person.” blocked; “Yes, I'm a live person.” allowed. |
| No prior bot label | Direct named greeting still rings; generic greeting → question-back legacy confirmation still works. |
| Six unsafe/data overlaps × two restricted configurations | Silence while labelled automated_assistant. |
| Assistant phrase cap / spacing | Maximum 12; minimum 8 seconds; exact threshold and early repeat tested. |
| Finish and post-human controls | Prior restrictions unchanged in probes; assistant path does not emit after confirmed human. |

**Delta run 1:** replayed all eight business transcript entries in `evidence/trial/call_b669b105d4774a49/trial.json` with their recorded relative timestamps, in both restricted configurations. The first generic greeting and Hi receive the fixed question and re-ask. After explicit automation, the four routing actions remain Representative, Agent, Agent, Agent at approximately 119.390, 128.070, 141.814 and 155.687 on the synthetic clock. No owner ring, bridge, arbitrary model prose, identity information or affirmative agreement; final state IVR. This is transcript replay, not an audio/live-call test; setup/disclosure and actual Delta responses to new speech are not simulated.

**Accepted bot-yes residual:** declaration → short-gap name → live-person question → bot “Yes.” reaches ringing_owner. Before owner acceptance, no conference movement occurs. Business-side keypad 1 does not bridge. The real `owner_accept` rejects empty digits and 2, and accepts 1, reaching owner_connected with exactly one fake business-leg conference move. Wrong-key owner-unavailable handling is stubbed to isolate acceptance. This residual is accepted as directed; no provider authentication or actual owner call was exercised.

## Test results, artifacts and limits

- `review.log`: 37 observation records; original replay/sequences plus quantitative/finish/post-human assertions. Exit 0, not an overall conformance assertion.
- `new_party.log`: adapted delta-25 boundary/N23/control/bridge cases, zero original confirmation failures. Exit 0.
- `clear_yes.log`: 103 extended observations. **27 mismatches:** 12 false confirmations (N23-R), 3 named-without-question rings (N24), and 12 misses of L2-approved identity affirmatives (Low compatibility observation). The remaining 76 pass their expectations. Exit **1** deliberately reports these mismatches.
- `l2.log`: **123 tests passed**, exit 0. Detector-only success does not establish engine conformance.
- [CASE-RESULTS.md](CASE-RESULTS.md): all inputs, timestamps, detector evidence, output, state and expectations, including the five explicitly scripted detector cases.
- Scripts: `fixtures/review.py`, `fixtures/new_party.py`, `fixtures/clear_yes.py`, `fixtures/l2.py`, shared `fixtures/support.py`, and `runner.py`.

Reproduce from this scratch directory:

```text
/usr/bin/python3 runner.py review new_party clear_yes l2
```

The runner prints each subprocess's exit status and saves its output; its own exit code is not an aggregate verdict. The relevant L1 test modules were read; the full socket-based suite was not run under the no-network limit. No full L1-suite pass is claimed.

The reused sandbox denies networking including loopback, real `~/.hermes-line/` reads, and test-process writes outside delta-26 fixtures (apart from /dev/null). HOME/TMPDIR/HERMES_HOME point into scratch, bytecode is disabled, and the hermes CLI is a nonfunctional stub. Per-script limits remain 60 CPU seconds / 60 seconds wall time, 8 MiB per file, 128 open files, process limit 1024, no core dumps; no aggregate memory/disk quota. All report/export/log writes are also within delta-26 scratch.

`tools/trial/` has no diff from the delta-25 reviewed main revision. No new harness finding is asserted and earlier stop/journal/reconcile checks are not repeated. The remaining engine confirmation defects block this retry. Main and the reviewed production files remained unchanged at the final revision check; repository status was clean.

No network, phone calls, provider access, protected-folder reads, installs, repository writes, commits or pushes occurred. No live retry is authorized or attempted by this review.
