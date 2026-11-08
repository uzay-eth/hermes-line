# Review R1b delta 16 (coordinator): hold command fixes N18, N19 (L6 round 11)

PURPOSE: Before one supervised airline hold test, the trial harness must never leave an unknown live call behind or
place a call after a stop. An earlier reviewer found two gaps; check the fixes.

TASK: Read-only review of L6 commit 0e477ac on `main` (`tools/trial/run.py`, `tools/trial/`, `tests/l6/`,
`docs/lanes/L6.md` round 11) against `evidence/reviews/R1-DELTA14.md` (N18, N19). Reuse the earlier scripts in
`/Users/mac/.hermes/cache/scratch/hermes-line-r1d14/` (adapt assertions to the intended behaviour). Check the
unresolved-create journal and startup block, recovery through the narrow Calls read, the stop/dispatch lock,
draining of in-flight creates, and hang-up of a SID that returns after stop.

LIMITS:
- Read only in the repo. Write only under `/Users/mac/.hermes/cache/scratch/hermes-line-r1d16/`.
- No network, no calls, no provider access. You may read `~/.hermes-line/approved_destinations.json` only.
- Do not commit or push.

DONE: `REVIEW-DELTA16.md` there with findings and a verdict for the hold command, harness side: yes or no.
