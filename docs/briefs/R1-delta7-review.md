# Review R1 delta 7 (coordinator): voice path before the first live Grok call

Read only, same LIMITS and disposable-scratch rules, under `/Users/mac/.hermes/cache/scratch/hermes-line-r1d7/`.
Main HEAD. Scope: `plugin/line/voice/` (L5) and its wiring in `plugin/line/engine/voice_port.py`,
`plugin/line/server/` media and digits routes, as used by an outbound call-only Grok trial to the owner's phone
(OTP off). You may not read `~/.hermes-line/`.
1. Secret handling: how `XAI_API_KEY` is read, held and sent; can it reach logs, transcripts, Hermes sessions,
   evidence files, the model, or Twilio?
2. Spend: can a call keep a Grok realtime session or the STT stream open beyond the call, after hangup, on
   reconnect loops, or during hold? Is there any unbounded retry against xAI?
3. Side effects from Grok tools (`ask_hermes`, `bring_owner`, `press_keys`, `report_outcome`, `end_call`,
   `send_verification_code`, `wait_on_hold`): all through the same L1/L2 gates as relay? Prompt injection from the
   callee's speech into Grok instructions.
4. Media route: token kinds per generation, replay, and closing on call end.
Write `REVIEW-DELTA7.md`. Verdict: ready for one outbound Grok call-only trial (max 3 min) to the owner's Spectrum,
call profile `hermes-line-calls`, fallback ingress without Auth Token: yes or no.
