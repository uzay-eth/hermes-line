# L6 round 9b (coordinator decision): owner-line demo with ring-back

`git merge main`. Decision: the trial and demo use the plugin's current contract, no live-leg transfer: the owner
calls the line, keys the PIN, asks for a call, confirms by keypad, hangs up; the plugin dials the approved task (Fi
line plays the business) and, when a human answers, rings the owner's Spectrum (new leg) and bridges on his
acceptance. Live-leg transfer goes to L1 later as an improvement; do not wait for it.
Extend `listen` (relay only): after the inbound owner call ends, keep service, tunnel and ACP alive until every task
approved during that call has ended or `--minutes` passes. Live adapter permits exactly these creates: the child
business leg to an allowlisted destination equal to the task's approved destination, and owner-ring legs to the
configured owner number; refuse any other create before any API call; never the line itself, never blocked
numbers; no retries. Balance re-check before each create (stop line $4.00). Evidence: inbound leg, child leg, owner
ring leg, conference events, SIDs, durations, masked transcripts, latencies; PIN only as attempt counts.
Tests offline with the fake adapter: PIN, keypad confirm, hangup, child dial, human answers, owner ring accepted and
bridged, owner declines, child unanswered, stranger cannot create a task, unexpected create refused.
Then round 10 when L1 round 16 is on main. No live run. LIMITS as before. DONE: tests, SHA, exact command.
