# R1 delta findings, follow-up (coordinator)

Read `/Users/mac/.hermes/cache/scratch/hermes-line-r1d/REVIEW-DELTA.md` (read only).

## L1 (do this FIRST, before the rest of round 5)
- M2 remains: concurrent duplicate Connect completions for one generation all wait at `machine.py` `asyncio.sleep(0.1)`,
  then each calls `relay_action`; the budget runs out and the call hangs up (reproduced in both auth modes). Make
  completion of a generation single-flight: the first request claims the generation atomically, later ones wait for
  and return its cached result; re-check after every await. Test with 3 concurrent posts while the socket is live,
  default budget, both modes: one reconnect, call alive, identical responses.
- N1 (your half): `send_secret` carries the approval generation and re-checks it on the loop right before the frame is
  queued; a send whose caller timed out is cancelled and can never send later. The sealed sink must not block the
  service loop. Production-sink integration test that overlaps an approval replacement with a delivery: loop never
  blocked, no frame after a reported failure.
Commit these as their own commit, then continue round 5.

## L4
- N1 (your half): do not hold `registry.gate` (a blocking threading lock) across `call_sink.send`. Validate the
  generation, release the lock, and pass the generation to the sink so L1 re-checks it on the loop. Keep browser
  locking separate. Test with a sink that needs a second thread to take the gate to complete.
