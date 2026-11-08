# L5 round 6 (coordinator): findings from the first live Grok call

`git merge main`. Evidence: `evidence/trial/call_374a992b53c044f9/trial.json` (owner call 4, 64 s, completed; Grok
connected 53 s, connect 1214 ms on human, first audio 490 ms, ledger +$0.096). Fix with theories first where the cause
is not obvious:
1. The disclosure was spoken twice, 2.2 s apart, at the start (20:45:32.5 and 20:45:34.7). Find which paths both
   speak it (relay greeting, `voice_open` for speech then `voice_close` "gate: dialing", Grok session greeting) and
   make it exactly once per call.
2. The STT transcript stores every partial as a new business line ("I can hear you." ... seven growing copies) and
   duplicates finals ("Good, how are you doing?" twice). Store only finals, once; partials may drive barge-in only.
3. `barge_in_events` is empty; check whether barge-in is wired on the Grok path at all and test it with a fixture.
4. Twilio price was still null at evidence time; fine, but the harness should re-read the call price once after 60 s.
   (Report to L6 if it is harness-side.)
Same LIMITS. DONE: tests, SHA.
