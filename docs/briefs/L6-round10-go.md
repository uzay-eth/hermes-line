# L6 round 10 go (coordinator)

L1 rounds 16 and 17 are on main (merge of 0764706). First fix the six `tests/l6/test_ringback.py` failures: round 16
now asks for seven digits when the destination is not the owner's own or previously approved number; key
`proposal['code']` (or whatever the proposal exposes) instead of a hard-coded last four. Then build round 10 as
briefed in `docs/briefs/L6-round10-airline.md`, using L1's new task options `owner_ring_timeout_s` and
`fallback_question` (ConversationRelay only) and the approval file `~/.hermes-line/approved_destinations.json`.
Do a `dry` form of the hold command with the fake adapter that runs the real engine through menu, hold, human,
owner ring timeout, fallback question, end. No live run. DONE: tests, SHA, exact live command.
