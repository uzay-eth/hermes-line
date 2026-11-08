# L6 round 11 (coordinator): R1 delta 14 N18, N19

`git merge main`. Read `evidence/reviews/R1-DELTA14.md` and its probes under
`/Users/mac/.hermes/cache/scratch/hermes-line-r1d14/fixtures/` (read only; copy into tests/l6). Fix both as the
review's "Smallest fix" lines say:
- N18: durable unresolved-create record written before every Calls POST (role, approved destination, task/call id,
  attempt time, absolute deadline), updated when the SID is known; never cleared just because known legs ended.
  Recovery reconciles through a narrow read (Calls list filtered by To, From and StartTime window) and hangs up any
  live match; if it cannot resolve, it refuses new trials and says exactly what to check. Report unresolved cleanup
  distinctly, never as success.
- N19: re-check stop and approval expiry after the balance read and every other blocking step, then commit to
  dispatch under one lock shared with stop; track in-flight creates and drain them before cleanup reports success;
  a SID returned after stop is hung up at once and stays recoverable if that fails.
Tests: response loss and crash before SID persistence for both roles, then a fresh startup; stop during balance read;
response after shutdown starts. No live run. LIMITS as before. DONE: tests, SHA.
