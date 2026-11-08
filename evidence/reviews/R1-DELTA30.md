# R1b delta 30 — partial promotion and single question slot

**Delta run 3 verdict: NO.** The original stopped-partial recovery and duplicate-question cases are fixed. However, promotion turns an incomplete “Yes” into human confirmation, and later finals that contradict or extend it are ignored or handled too late to prevent the owner ring. The same prefix suppression bypasses unsafe-offer silence. The question-budget counter also misses a promoted-partial path.

## Revision and method

Reviewed main `bb418646949bb5478d7b329d491f68cf6ae6581b`, containing `54d0fc72c08cb103b93a2034acff5cbd10cc6be3`, against `evidence/reviews/R1-DELTA29.md`. Read the change, round-32 tests and section 44, and traced promotion, final suppression, question slots and confirmation. Main and the reviewed production files remained unchanged at the final check; checkout clean. BRIEF's verified hash remains unchanged. No tracked Graft index was available; source/diffs were used without rebuilding.

Copied the prior offline runner and fixtures into delta-30 scratch. `promotion.py` adds partial-versus-final comparisons through public `CallSession.offer`, the real consumer/detector/engine and real owner-ring method, using fake authorization, placement, transport and storage. `r31.py` reruns the adapted delta-28/29 timing, fallback, config and replay probes. Both use quiet=1.2 with a controlled monotonic clock driving the real asyncio timers. Clock steps explicitly service the new 2.5-second promotion deadline before testing the subsequent quiet period. `wall_quiet.py` separately uses actual elapsed time. No provider is invoked.

## N30 — Medium — promoted prefixes become authoritative, and corrected finals cannot retract the decision

Sources: `plugin/line/engine/machine.py:495` (`_duplicate_of_promoted`), `:504` (`_partial_watch`), and the normal strict-confirmation/human path.

The watcher injects partial text as an ordinary final prompt. Its provisional origin is not carried into the confirmation policy. A complete shared-list match in that partial therefore sets yes_confirmed and human_confirmed. The final-suppression rule then drops any normalized final equal to **or starting with** the partial for ten seconds, without checking utterance identity or whether its meaning changed. It does not even require a word boundary.

Reproduction, with an actual fixed question already sent:

| Time | Event | Result |
|---|---|---|
| 100 | Named greeting, final | Question scheduled |
| 101.25 | Quiet completes | Fixed live-person question sent |
| 102 | Partial `Yes` | Watcher starts |
| 104.51 | Promotion serviced | `Yes` classified HUMAN; confirmation flags set; connecting line waits for quiet |
| 104.8 | Corrected final | Dropped or queued as described below |
| 106 | Quiet completes | Connecting line sent; one fake owner placement; ringing_owner |

All four corrected finals below **prevent confirmation when supplied alone**. Each nevertheless rings after partial `Yes`:

- “Yes, I'm not a live person.”
- “Yes, how can I help?”
- “Yes, would you like to make a payment?”
- “Yesterday I called about my bag.”

All four are logged as `duplicate_final_dropped`. The last example demonstrates the missing word boundary: normalized “yesterday” starts with “yes”. The first is an explicit contradiction of liveness; the others do not satisfy the whole-reply affirmative rule. This differs from the accepted residual of a bot giving a complete, unqualified yes.

A genuinely different final also fails to retract confirmation: after partial `Yes` is promoted, “No, I am an automated assistant.” is **not** prefix-dropped. It updates business activity and waits in the frame queue while the connecting line awaits quiet. The call has already entered the human path. The owner is rung, and the correction later receives the fixed holding response rather than fresh classification. Thus merely narrowing the prefix comparison would not fully fix the defect.

### Assistant-phrase counterpart

In a known-bot conversation, partial “How can I help” at 110 is promoted at 112.51. The real final “How can I help you make a payment?” arrives at 112.8 and is dropped. Hermes sends **“Baggage question.” at 114**.

The full final alone, with the same recent bot history, is classified automated_assistant and produces **silence** under unsafe-offer precedence. The promoted version loses that veto. No payment agreement or data disclosure occurs in the probe; the failure is unauthorized deviation from the required silence behavior.

Because duplicate finals are dropped before activity bookkeeping, they also fail to extend the quiet gate. More broadly, a provisional fragment has been granted authority that the completed utterance does not have.

**Required correction:** recover from abandoned partials without treating them as final identity/authorization evidence. Retain provisional status through detection and output decisions; changed finals must be processed as corrections, not discarded by string prefix. A correction arriving before a deferred outward action must be able to cancel/re-evaluate it. The safety check needs the completed meaning, not just the already-promoted fragment.

## N31 — Low — the question budget still misses a promoted-partial route

Sources: `machine.py:930` (`_maybe_confirm`) and `:968`–`:1000` (question slot/on_sent); `_new_party_check` supplies the budget callback, whereas the generic uncertain path does not.

The exact prior four-attempt stimulus is retained: final named greetings at 100, 122, 144 and 166, each followed by repeated partial activity, then quiet and a fresh greeting at 191. The observed question sends are **108, 152 and 193**, while `new_party_questions` is **2**. The last promoted/uncertain path sends a question without spending a new-party slot. A simpler partial-only generic utterance also produces one spoken question while this counter remains zero.

The original late-HUMAN duplicate is fixed and its merged callback counts correctly. This residual is counter coverage across routes, not evidence that the old duplicate still occurs. The result does not establish a global four-question allowance across all fixed-question routes. Align counting/spacing with actual sends under the intended strict-call budget, or explicitly narrow the advertised budget to the particular route it counts.

## Prior fixes and liveness

| Probe | Result |
|---|---|
| Partials at 100, 100.3, 100.6, then stop | Promoted after the inactivity deadline; one fixed question at 104.36. Original N26-R stopped-partial silence is fixed. |
| Timeout question pending, late HUMAN arrives before quiet | `question_joined`; exactly one question at 102.7; budget 1; no ring. Original N29 duplicate fixed. |
| Final arrives before promotion deadline | No promoted_partial event. |
| Identical real final arrives after promotion | Dropped; no second question. This identical-text control passes; it does not justify dropping changed meanings. |
| Superseded question and newer utterance | Old attempt spends nothing; replacement proceeds. |
| Connecting and fallback closing | Wait for ordinary 1.2-second quiet; independent wall-clock question/connecting gaps about 1.25 s each. |
| Delayed assistant spacing | Too-early second phrase is skipped; spacing derives from actual sends. |
| Fallback table | Five nonempty replies are `unverified`; silence `unanswered`; neutral closing and quoted, unverified summaries retained. |
| Live config factory | Actual trial config body returns EngineConfig quiet=1.2 and enables the gate. Bare-object missing attribute falls back to zero, as documented. |

A remaining liveness limit is explicit: **continuous partial-only traffic every 0.3 seconds for 21 seconds produces no decision, question or output.** The promotion timer is measured from the *last* partial, so it never expires without a 2.5-second gap. The ten-second quiet cap only applies once speech is queued. This is observed behavior, not a claim that continuous actual business speech should always be interrupted. If the partial stream stalls and then goes quiet, promotion now recovers; if it never pauses/finalizes, the call can remain in this state until external or duration limits end it. The requested universal “no silent path” guarantee is therefore not established.

The question-slot correction works for the tested timeout/late-verdict race. No unauthorized bridge was observed: owner keypad 1 is still required. That retained bridge guard does not repair false owner-ring decisions on provisional prefixes.

## Delta runs and regression results

All business transcript entries from Delta runs 1 and 2 were replayed with their recorded relative times and quiet=1.2. Both remain IVR with no human confirmation, owner placement or bridge. Outputs remain the fixed routing/question lines described in delta 29 and meet the ordinary replay quiet interval. These recorded final-transcript replays do not contain the new adversarial partial/correction pairs and therefore do not expose N30 by themselves.

- `promotion.log`: 14 observations; **six failing comparisons** (four prefix-corrected owner rings, one non-prefix correction, one assistant-phrase case), seven passing controls, one continuous-partial observation. Exit 1.
- `r31.log`: 17 adapted prior observations; 16 pass and the question-count equality check fails. Exit 1. Its old case titles are preserved for traceability, even where the formerly failing behavior now passes.
- `wall_quiet.log`: actual wall-clock timing check passes.
- `clear_yes.log`: 103/103 confirmation observations pass using completed transcripts, quiet=0.
- `ring_gate.log`: 35/35 boundary observations pass under the inherited settings, quiet=0.
- `review.log`, `new_party.log`: inherited N20/N21/N23, phrase, finish and owner-key controls pass under their stated settings.
- `l2.log`: **130 tests passed**. These tests do not validate treating promoted fragments as completed utterances.

[CASE-RESULTS.md](CASE-RESULTS.md) contains full case inputs, output timestamps, state, detector evidence and events. Reproduce from this scratch directory:

```text
/usr/bin/python3 runner.py promotion r31 wall_quiet l2 review new_party clear_yes ring_gate
```

The runner reports individual exits; its own exit code is not an aggregate verdict. The active adapted timing entry points are `r31.py` and `promotion.py`; copied `quiet.py` provides helpers rather than current expectations. Relevant L1 tests were read, but no socket-based L1/L6 suite was run. No full L1 pass or live routing success is claimed.

The existing sandbox denies networking, including loopback, and real `~/.hermes-line/` reads. HOME/TMPDIR/HERMES_HOME are scratch paths, bytecode is disabled and hermes is a nonfunctional stub. Test writes stay in delta-30 fixtures and /dev/null; all exports/logs/reports stay under delta-30 scratch. Per-script limits remain 60 CPU seconds and 60 seconds wall time, 8 MiB per file, 128 open files, process limit 1024 and no core dumps; no aggregate memory/disk quota. Long timing scenarios use controlled clocks. No approval-file read was needed.

No network, calls, provider access, protected-folder reads, installations, repository writes, commits or pushes occurred. The NO verdict is based on the new promotion behavior, independent of any future owner go.
