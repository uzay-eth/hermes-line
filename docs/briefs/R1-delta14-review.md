# Review R1 delta 14 (coordinator): hold command (L6 round 10)

Read only, same LIMITS and disposable-scratch rules, under `/Users/mac/.hermes/cache/scratch/hermes-line-r1d14/`.
You may read `~/.hermes-line/approved_destinations.json` (no secrets in it); no other file under `~/.hermes-line/`.
Main HEAD, L6 commit 8e8a9c2 (`docs/lanes/L6.md` round 10, `tools/trial/run.py hold`). L1 round 18 (your N16/N17)
is in progress and will be a separate short delta.
Check: only the approved business and the owner can be dialled (role-aware, approval expiry, blocked list, never the
line or Bora); no retries or redial; the 45-minute global cap and per-leg TimeLimit; the 60 s balance guard and
projected-cost stop at $4.00 (what if the balance read fails); cleanup of both legs and the conference on every exit
including crash and Ctrl-C; the live JSON events (human_detected before the owner create); evidence masking.
Write `REVIEW-DELTA14.md`. Verdict for the hold command, harness side: yes or no.
