# L5 round 2 (coordinator)

First `git merge main` (your bf168b9 is merged; L1 and L4 are fixing review findings in parallel; do not touch
their paths).
1. Build the DTMF fallback you designed, inside `plugin/line/voice/`: a function L1 can call to send menu digits by
   briefly leaving the stream (REST call update to TwiML `<Play digits>` then `<Connect><Stream>` again with a fresh
   one-time token) and a policy that picks tones first and the fallback when the menu does not react (state the
   signal you use). It must refuse any digits that come from a verification code: codes go only through the sealed
   path. Fake-adapter tests, including "code digits refused on the fallback".
2. Add to `docs/lanes/L5.md` section 2 the exact L1 calls for this fallback.
3. Write `docs/lanes/L5-live-test-plan.md`: the first live Grok checks once a key exists, each with expected minutes
   and cost at list price (Grok connect time on human answer, first audio, barge-in, tones heard by a real menu, STT
   quality over hold music, session length limit), to the owner's own phones only, total under $2 of xAI usage.
Same LIMITS as your brief. DONE: `tests/l5/` output, commit SHA.
