# L6 round 9 (coordinator): owner-line demo trial end to end

`git merge main`. Target trial (demo beat 1), all on the owner's own phones: Fi forwarding stays "Don't forward";
the owner calls the plugin line [the line] directly from Spectrum, enters the PIN, asks Hermes to call his Fi line (the
Pixel plays the business), Hermes dials it, and bridges the owner leg in when the Pixel answers.
Extend `listen` (relay only) so one run covers this: after the inbound call is admitted, keep the service, tunnel and
ACP alive until every task created during that call has ended or `--minutes` passes; outbound legs from those tasks
use the same allowlist, blocked list, caller ID option and balance re-check as `call` (refuse anything else before
any API call; never the line itself, never Bora); evidence covers the inbound leg, the outbound leg and the bridge
(SIDs, durations, masked transcripts, latencies, conference events). Call profile and PIN handling unchanged.
Tests offline with the fake adapter: owner leg stays up while the outbound task rings, bridge, owner hangs up first,
outbound not answered, a stranger cannot create a task. No `call`/`listen` run. LIMITS as before.
DONE: tests, SHA, the exact command.
