# R1b delta 25 — new-party confirmation and unsafe-offer precedence

**Delta retry verdict: NO.** The engine can ring the owner after a possible new party answers the live-person question with another question, including a payment offer. No affirmative answer is required on that path. This conflicts with this review's explicit “only a clear yes” requirement. Owner keypad acceptance still prevents an automatic bridge; the separately requested bot-says-yes residual is acceptable for this supervised test, but does not close the confirmation bypass.

## Scope and evidence

Reviewed main export `6b1c1e3a5367250700b7767f73207846d797724f`, containing L2 `a33571d` and L1 `bdb693a`. Read the brief, delta-24 report, changed detector and engine code, L2 round-7 tests/docs, L1 sections 38–39 and round-27 tests. Main later advanced to `2e39742dbba8d8f33f9affd5b96d9ad8880ef7c9`; `plugin/line/engine/` and `plugin/line/detect/` remained identical to the export at that check. The checkout was clean.

Reused delta-24 fixtures in this scratch folder. `fixtures/review.py` no longer invokes the removed `_track_assistant`. Each business utterance now enters **`CallSession.offer` → real queue consumer → real frame/speech handling → real HoldTracker → real engine policy**. Unique transcript sequence IDs are supplied by the fake store. The model-turn fake raises if invoked. Sessions are fixture-constructed; transport output, storage, worker execution, provider movement and owner ringing are faked. This exercises the engine without a listening socket or provider.

- `review.log`: 37 observation records, including the actual Delta run-1 business transcript with its recorded relative timing, all nine delta-24 adversarial sequences, 18 phrase-prompt cases, phrase cap/spacing, finish restrictions and post-human assistant suppression.
- `new_party.log`: boundary probes, six transfer sequences, eight confirmation-answer variants, no-boundary attention and music controls, actual-detector question-budget checks, three owner-acceptance checks and 12 mixed unsafe-offer cases. **Three confirmation variants fail the requested rule.** The script exits 1 deliberately for those failures.
- `l2.log`: **116 tests passed**. This validates the authored contract and regressions, not the stronger clear-yes requirement below.
- [CASE-RESULTS.md](CASE-RESULTS.md) records every sequence input, timestamp, label/evidence, speech and resulting state, plus quantitative results. Raw logs retain events.

Both information-only and other restricted fixtures use owner mode; no claim is made about a separate agent-mode integration run. Approved intro names are `Delta` and `Delta Air Lines`; the synthetic category is baggage. No approval-file read was needed.

## N23 — Medium — a question back bypasses the new-party clear-yes requirement

Sources in the reviewed tree: `plugin/line/detect/__init__.py:135` (`_confirmation_reply`) and `:222` (boundary confirmation); `plugin/line/engine/machine.py:683` (`_new_party_check`) and `:784` (unconditional handling of L2 HUMAN).

The following real-ingress sequence uses one-second gaps, so no accidental eight-second silence boundary is needed:

| Time | Business utterance | Actual result |
|---|---|---|
| 100 | I'm Delta's automated assistant. | automated_assistant; “Representative.” |
| 101 | Let me connect you to an agent. | hold_message + transfer; silence |
| 102 | Hello, how can I help? | unknown + possible_new_party; “Am I speaking with a live person?” |
| 103 | Hello, how can I help? | **human/high; human_confirmed=True; owner-ring boundary invoked** |

The last input can instead be **“Is that correct?”** or **“Would you like to make a payment?”**, with the same human confirmation and owner-ring result. Hermes says “Thank you. Please hold for just a moment while I connect Fixture owner.” No yes was received. Synthetic ground truth may remain the same bot; the transfer statement alone does not establish a human.

Controls after the same prefix: “May I have your confirmation number?”, “No.” and “Maybe.” stay unconfirmed and silent. “Would you like us to call you back?” stays automated_assistant and silent. “Yes, I'm not a live person.” also stays automated_assistant. Thus this is a specific precedence problem, not failure of every negative or explicit-bot control.

Cause: L2's legacy `_confirmation_reply` accepts questions beginning with words such as how/is/would. The boundary branch turns that into HUMAN before L1's possible-new-party handler can enforce its clear-affirmative test. L1's `_on_detection` immediately trusts HUMAN and rings. Unsafe-offer precedence inside `assistant.choose` is not reached when L2 labels the payment question HUMAN.

L1 section 39 and `RealDetectorN21Tests.test_N21_question_budget_one_per_20_seconds_four_per_call` explicitly preserve the question-back exception. That documents the discrepancy; it does not meet the coordinator's current “only a clear yes” instruction. The all-green authored detector suite therefore does not resolve this finding.

Impact: an unconfirmed bot can trigger human-only fixed speech and an owner ring without answering the live-person question affirmatively. This did **not** accept payment, disclose information or bridge automatically in the probes. Severity is Medium because the new confirmation gate fails at the exact transition under review.

Required correction: when a possible-new-party confirmation is pending, ensure the end-to-end L2/L1 path requires the agreed affirmative answer; generic questions and unsafe offers must not bypass it through a HUMAN label. Add real-detector/engine regressions for the three failing inputs. If the coordinator intentionally wants question-back confirmation, that would be a changed acceptance rule, not closure under the present task.

## Delta-24 findings and other checks

| Item | Result |
|---|---|
| N20, same named bot within 1 or 7.999 seconds | Now unknown + possible_new_party; asks the fixed question; does not ring directly. The short-gap override is fixed. |
| N20, original 10-second spacing; exact 8-second boundary | Still HUMAN and rings directly. L2 deliberately treats ≥8000 ms without business speech as a boundary. The same bot can pause and name itself; this remains a classification limitation, not a demonstrated speaker change. The exact original case is **not** reported as fixed. |
| N21, unnamed greeting after transfer or music | Now asks the fixed live-person question. A clear yes then reaches owner ringing. Transfer variants, including “Please wait while I connect you”, are silent hold_message results with the exact transfer marker. |
| N21, no boundary but “Hello? Are you there?” | Now possible_new_party; fixed question; a subsequent yes rings. |
| Single source of recognition | L1's private bot/name/transfer helpers are removed. Its assistant latch is set only from the automated_assistant label. Hold/transfer handling follows L2; there is no second engine transfer regex. |
| New-party question limits | Real tracker and engine ask at 101, 121, 141 and 161 seconds. At 105 and 120.999 they remain silent. At 181 the budget is exhausted and the fixed assistant path says “Agent.” No fifth question or human confirmation. |
| N22, unsafe/data overlap while classified automated_assistant | Six mixed prompts in each of two restricted fixtures: silence, no data or affirmative output. `choose` checks unsafe_offer before data_request. Callback-number, payment, change-date and card-number cases pass. |
| Original 10-second phrase prompts | Callback offers stay silent. Payment/change prompts can now be possible_new_party and get the fixed live-person question because the gap is a boundary. This is distinct from an assistant phrase; the broader claim that every original unsafe prompt is silent would be false. See the case table. |
| Assistant phrase limits | At most 12, with no interval below 8 seconds; 7.999-second repeat suppressed and exact 8-second repeat allowed. |
| Finish/post-human | Failed/callback_expected finishes refused while still routing; information-only done refused. Other restricted done retains its previous permission. Direct assistant speech adds no output after human confirmation. Public-ingress post-human speech uses the fixed holding path in the information-only replay. |

The 8-second boundary is explicitly the new L2 contract, and the tests confirm it rather than silently changing the old replay timing. It reduces missed unnamed-agent handoffs but cannot prove acoustic liveness. N20 is therefore closed only for the short-gap/no-boundary case. N21's latch/transfer mismatch is fixed; N23 concerns what subsequently counts as confirmation. N22's **assistant phrase selection** precedence is fixed; it is not a global unsafe-offer veto over every detector label.

## Delta run 1 replay

Replayed all eight business transcript entries from `evidence/trial/call_b669b105d4774a49/trial.json`, preserving relative timestamps, in both restricted fixtures. Initial “Thanks for calling.” and “Hi.” receive the fixed live-person question and fixed re-ask. Once the line explicitly says it is not a live person, the actions are **Representative, Agent, Agent, Agent**, at approximately 119.390, 128.070, 141.814 and 155.687 seconds on the synthetic clock. The intervening declaration and add/update prompt are silent under spacing/unsafe rules. No owner ring, bridge, unrestricted model prose, identity data or affirmative agreement occurs. The call remains in IVR.

This is a transcript replay with a fresh fake session, not an audio replay or evidence that a live Delta retry would reach an agent. Initial provider setup/disclosure is outside this replay.

## Bot answers yes: acceptable residual, bridge still explicit

In the same-bot fixture: declaration at 100 → name at 101 → Hermes's live-person question → bot says “Yes.” at 102. The real detector labels it HUMAN and the engine requests the owner ring. That false liveness assertion is unavoidable for a transcript-only yes/no check and is **acceptable for the specifically supervised retry as a residual**, because it cannot connect the owner without a separate owner action.

Verified with the real `owner_accept` method and a counted fake conference move:

| Input after bot yes | Result |
|---|---|
| No owner acceptance yet | ringing_owner; no conference move; handoff_pending false |
| Keypad 1 on business frame ingress | Still ringing_owner; no conference move |
| Owner acceptance with empty digits | False; no conference move |
| Owner acceptance with 2 | False; no conference move |
| Owner acceptance with 1 | True; owner_connected; exactly one business conference move |

For the wrong/empty-owner-key cases the subsequent owner-unavailable action is faked to isolate acceptance; no claim is made about its fallback timing. Static ingress review confirms `/owner/accept` correlates the owner leg and passes `Digits` into `owner_accept`; only successful acceptance returns conference TwiML (`plugin/line/server/ingress.py:188`). Provider authentication/REST verification were not exercised. The hold task is a direct outbound owner-mode call, not an existing-parent live-owner bridge.

The accepted bot-yes residual does not authorize the different no-yes behavior in N23.

## Harness, reproduction and limits

No new harness finding is asserted. This review does not repeat earlier stop/journal/reconcile audits. Relative to previously reviewed `b137b1b`, the current `tools/trial/hold.py` changes add assistant-phrase telemetry and pass `ivr_category` only when present in the approval row; intro names still come from that row, defaulting to empty. The engine/detector finding alone blocks the combined retry verdict.

From this scratch directory:

```text
/usr/bin/python3 runner.py review new_party l2
```

`review` exits 0 for its observation/quantitative checks; that is not an overall conformance pass. `new_party` exits **1**, listing the three failing confirmation variants. `l2` exits 0, 116 tests passed. The runner prints individual subprocess exits and saves logs; its own exit status is not an aggregate test verdict.

Tests run under sandbox-exec denying all networking, including loopback, real `~/.hermes-line/` reads and writes outside this scratch fixture subtree. Source is read-only to the subprocesses. HOME/TMPDIR/HERMES_HOME are scratch locations, bytecode is disabled, and the hermes command is a nonfunctional stub. Per script: 60 CPU seconds, 60-second wall timeout, 8 MiB per-file limit, 128 open files, process limit 1024, no core dumps; no aggregate memory/disk quota.

Socket-based L1 integration suites were not run under the no-network constraint. The known L5 rehearsal update is excluded and contributes no finding. No tracked Graft index was available in the export; source and diffs were used without rebuilding. No network, phone calls, provider access, protected-folder reads, installations, repository writes, commits or pushes occurred. All created files are under this delta-25 scratch directory.
