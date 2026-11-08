# Review R1 delta 2 (coordinator)

Your delta review on dae1b78 left M2 partly fixed and found N1. Fixes are merged on local main at 2976a70
(L1 commit 7da6dcf for M2 + N1 L1 half; L4 commit fcb9881 for N1 L4 half; L1 commit 9f04274 wires the voice path).
Read only, same LIMITS and the same disposable scratch rules as before, now under
`/Users/mac/.hermes/cache/scratch/hermes-line-r1d2/`.
1. M2 and N1: re-run your two reproductions against 2976a70. Fixed / not fixed, file:line, and whether the new tests
   exercise them.
2. New since your last pass: the Media Streams ingress route, its token kinds and the digits route
   (`plugin/line/server/`, `plugin/line/engine/` changes in 9f04274; `docs/lanes/L1.md` section 12). Check them with
   the same lens as R1 check 3 (forgery, replay, token reuse, fail-open). Skip `plugin/line/voice/` internals except
   where a voice tool can cause a side effect (dial, owner ring, DTMF, OTP); check those go through the same gates.
Write `REVIEW-DELTA2.md` there. Verdict: ready for the first call-only live trial to the owner's own two phones
(OTP off via `sms_source: none`, browser fill off, fallback ingress without Auth Token, ConversationRelay transport
since there is no xAI key), yes or no.
