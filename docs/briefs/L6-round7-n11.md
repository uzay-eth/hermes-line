# L6 round 7 (coordinator): R1 delta 8 N11

`git merge main`. Read `evidence/reviews/R1-DELTA8-voice.md` section N11 and the probes
`fixtures/probe_delta8.py`, `fixtures/probe_real_close_shutdown.py` under
`/Users/mac/.hermes/cache/scratch/hermes-line-r1d8/` (read only; copy into tests/l6 if useful).
Fix in `tools/trial/grok_budget.py` and `tools/trial/run.py`: connected seconds must be charged even when close is
cancelled or still in flight. Account at close start in a `finally` that also runs on cancellation; `close_all()`
waits for accounting in flight; `finish()` refuses to settle while a socket is still closing, and settles from wall
time since connect when the close never completed; a later real close can only raise the charge, never lower it.
Service teardown that cancels loop tasks must not skip accounting. Tests: both reviewer probes inverted, plus a
crash mid-call (the next run's preflight reconciles from the journal with the worst case for that run).
No `call`/`listen`. LIMITS as before. DONE: tests, SHA.
