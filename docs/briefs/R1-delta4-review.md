# Review R1 delta 4 (coordinator)

Read only, same LIMITS and disposable-scratch rules as before, under `/Users/mac/.hermes/cache/scratch/hermes-line-r1d4/`.
Main is at 58a17b1. Diff since your last pass: `git diff 1d16bfd 58a17b1 -- plugin/ tests/l1 tests/l2`.
Focus:
1. Inbound callbacks over a forwarded line (L1 r7 part 2, `docs/lanes/L1.md` sections 15-16): admission of forwarded
   calls, trust in `ForwardedFrom`/`CallerName`, matching a callback to a task, the one owner ring per inbound call
   and its press-1 consent, what an unknown caller can make Hermes do.
2. The rule-12 guard (L1 r8 item 1): can a call start with more tools than the call tools (parsing of `/tools`,
   deferred-tool form, races between check and use, env flag stripping)?
3. N3 fix, toll-free allow and `blocked_destinations` (L2 r3), browser fill removal (unreachable?).
4. The r7 silence changes: filler lines, re-ask, hand-over: can they cause a side effect without the gates?
Write `REVIEW-DELTA4.md`. Verdict, two parts: (a) a second outbound call-only trial to the owner's Spectrum,
(b) an attended inbound trial (owner calls the Fi line, forwarded to the plugin line; owner ring on), both OTP off:
yes or no each.
