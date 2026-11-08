# L1 round 14 (coordinator): R1 delta 10 findings (owner line)

`git merge main`. Read `evidence/reviews/R1-DELTA10-5b654a4.md` and the probes under
`/Users/mac/.hermes/cache/scratch/hermes-line-r1d10/fixtures/` (read only; copy into tests/l1 if useful).
Fix N12, N13, N14 and N15 as the review's "Smallest fix" lines describe. Decisions already taken:
- N12: the yes counts only after the fixed read-back (task, destination, generation) has been fully spoken; arm then,
  expire after 20 s, invalidate on barge-in, replacement or any unrelated turn; queued or earlier speech never counts.
- N13: persistent attempt budget per line across calls and restarts: 3 failures, then owner mode is locked for 15
  minutes, 10 failures in 24 h lock it until the owner unlocks from chat (`/line unlock-owner`); each lockout sends one
  chat notice. Caller ID is not the key.
- N14: DTMF-only PIN entry. Remove spoken PIN entry; the prompt says "key your PIN, then pound". Also scrub the
  configured PIN digits from transcripts and model input as a backstop (spoken or keyed), without logging it.
- N15: as the review says; test the pause branch.
Keep the flaky-test item from my earlier note. Same LIMITS. DONE: tests (each finding's probe inverted), SHA,
README changes for DTMF-only and lockout.
