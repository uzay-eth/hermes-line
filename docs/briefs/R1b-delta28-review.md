# Review R1b delta 28 (coordinator): Delta run 2 false human (L2 round 9, L1 round 30)

PURPOSE: Delta run 2 (`evidence/trial/call_2243b7f205194ab5/trial.json`) rang the owner for Delta's assistant: its
self-introduction was cut off ("I'm Del"), and the slow model path called a brand question human. A third run needs
your YES and a new go from the owner.

TASK: Read-only review on `main` of L2 commit fa40c3c (`plugin/line/detect/`, `tests/l2/`, `docs/lanes/L2.md` round 9)
and L1 commit ad53768 (`plugin/line/engine/`, ConversationRelay TwiML partial transcripts,
`quiet_before_speak_s`, `tests/l1/test_r30_delta_run2.py`, `docs/lanes/L1.md` section 42). Rules: on restricted
tasks the owner is rung only after the fixed live-person question and a shared `clear_yes()` reply, in every call;
Hermes waits for 1.2 s of business quiet before any line except the opening disclosure; the model path cannot give
human high without a name or a clear yes; the fallback closing is neutral and a non-answer is `unanswered`.
Replay Delta runs 1 and 2. Probe: partial transcripts that never finalise (can Hermes stay silent forever, and does
the 20 s / 4 question budget still advance), a bot that says "yes" (accepted residual, owner must press 1), and a
real agent who answers our question with "Yes, how can I help?" (fails closed; state the cost).
Known: one L6 dry-run test is being updated for the clear-yes step; ignore it.

LIMITS:
- Read only in the repo. Write only under `/Users/mac/.hermes/cache/scratch/hermes-line-r1d28/`.
- No network, no calls, no provider access. Read only `~/.hermes-line/approved_destinations.json` under that folder.
- Do not commit or push.

DONE: `REVIEW-DELTA28.md` there with findings and one verdict for Delta run 3: yes or no.
