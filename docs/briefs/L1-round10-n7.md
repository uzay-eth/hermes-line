# L1 round 10 (coordinator): N7

Read `evidence/reviews/R1-DELTA5-3c9eccb.md` section N7 and the probe `fixtures/probe_cancel.py` under
`/Users/mac/.hermes/cache/scratch/hermes-line-r1d5/` (read only; copy into tests/l1 if useful).
An owner Hang up submitted while the agent starts is queued behind the whole approve/startup/dial operation, so the
call is dialled and then hung up. Fix: a stop request must take effect before dispatch. For example, record the stop
intent atomically outside the command queue (or let `hang_up` for a call in CALLING bypass the queue), and have the
final pre-dispatch check read it; clean up the provisional session. Also map an explicit owner cancel for a call
still starting. Tests through the real owner command path (`/line hangup` routing in `hermes_glue.py` and the
service command queue), not by writing task status directly: hang up during startup -> zero provider calls, owner
gets a clear "stopped before dialling" result; hang up after dispatch keeps today's behaviour.
Same LIMITS. DONE: test output, SHA.
