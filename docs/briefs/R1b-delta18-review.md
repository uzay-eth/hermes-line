# Review R1b delta 18 (coordinator): N18 residual fix (L6 round 12)

PURPOSE: Same hold test. Confirm the harness can no longer clear an unresolved call attempt on the strength of a
different call.

TASK: Read-only review of L6 commit fa68da4 on `main` (`tools/trial/`, `tests/l6/`, `docs/lanes/L6.md` round 12)
against `evidence/reviews/R1-DELTA16.md`. Re-run `fixtures/review_probes.py` from
`/Users/mac/.hermes/cache/scratch/hermes-line-r1d16/` (adapt assertions), and check the new `reconcile` command
(coordinator flag, masked output, clears only after confirming no candidate is live).

LIMITS:
- Read only in the repo. Write only under `/Users/mac/.hermes/cache/scratch/hermes-line-r1d18/`.
- No network, no calls, no provider access. You may read `~/.hermes-line/approved_destinations.json` only.
- Do not commit or push.

DONE: `REVIEW-DELTA18.md` there with findings and a verdict for the hold command, harness side: yes or no.
