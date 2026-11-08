# L1 round 5 (coordinator, queued: send after round 4 is merged)

First `git merge main`. Wire L5's voice path into the engine exactly as `docs/lanes/L5.md` section 2 lists
(points 1-8 plus the DTMF fallback calls), with `tests/l5/test_l1_rehearsal.py::L1Port` as reference. Do not edit
`plugin/line/voice/`; ask the coordinator if a signature there must change. The stream route gets the same
authentication as the relay (with the round 4 fixes: per-generation tokens, no replay). Without `XAI_API_KEY` every
call keeps using ConversationRelay unchanged. Tests: an end-to-end fake call over Media Streams through your real
ingress (hold with zero voice-provider seconds, human, Grok tool call into Hermes, owner bridge, end), plus all
existing suites (l1, l2, l4, l5) passing. Same LIMITS as your brief. DONE: test output, commit SHA.

## Added at send time (coordinator)
On main dae1b78 (your round 4 + L5 round 2 merged), `tests/l5/test_l1_rehearsal.py::RehearsalTests::test_owner_mode_bridges_and_hands_off_the_stream`
fails: the conference TwiML request returns an empty `<Response></Response>` instead of `<Conference`. Diagnose first:
if your round 4 changed the conference route contract (generation, verification), make the stream path follow it and
write the new expectation for L5 in `docs/lanes/L1.md`; if it is an L1 regression (an empty Response hangs up a live
business leg), fix it with a test. Do not edit `tests/l5/`.
