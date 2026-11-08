# L6 round 3 (coordinator): inbound trial mode

`git merge main` (44007c0: L1 round 7 forwarded callbacks, settings `forwarding_number`, `inbound_ring_owner`,
`reply_budget_s`; L2 round 3 `policy.blocked_destinations`; the coordinator added `--caller-id fi` and
`blocked_destinations: [BORA]` to `tools/trial/run.py`, keep both).
Add `tools/trial/run.py listen --minutes N --i-am-the-coordinator` (N 1-10): starts the service and tunnel, points the
voice URL (and status callback) of the plugin line at the tunnel through the Twilio API, waits for one
inbound call (a forwarded Fi callback or a direct call from an allowlisted number), writes evidence like `call`, and
on exit ALWAYS restores the number's previous voice settings (read them first and save them under the trial root;
restore also from `down` and on a crash at next start). Config: `forwarding_number` = the Fi line,
`inbound_ring_owner` on, owner = Spectrum. SMS URL is never touched. The Bora number and any number other than [the line]
are never read or written except the account balance.
Also add a `--hold-sim` note to `docs/lanes/L6.md` only (no code): how a later trial would play a hold recording from
the Fi phone. Tests offline with the fake adapter, including "restore runs after a crash" and "never touches another
number". Do not run `listen` or `call`. LIMITS as your brief; the Twilio API write is allowed only in code, not run.
DONE: test output, SHA, the exact `listen` command.
