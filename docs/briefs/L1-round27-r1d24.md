# L1 round 27 (coordinator): R1b delta 24 N20, N21, N22 (engine side)

Finish round 26 first (it can share the commit). Read `evidence/reviews/R1-DELTA24.md` and the scripts under
`/Users/mac/.hermes/cache/scratch/hermes-line-r1d24/` (read only). L2 round 7 runs at the same time with this
contract: a possible new party after a bot is `unknown` with evidence `possible_new_party`; a transfer line is
`hold_message` with evidence `transfer`; L2 is the single source of transfer recognition.
- N20/N21: drop L1's own transfer regex and the rule that clears or sets `assistant_declared` from L1 text checks.
  Use L2 labels only. On `unknown` with `possible_new_party`, ask the fixed live-person question (the existing
  confirmation path). A clear yes moves to the human path (owner ring as today); "I'm not a live person" or any bot
  answer keeps the assistant path. At most one question every 20 s and 4 per call; after that keep the assistant path.
  A named `human` (high) label still rings the owner directly.
- N22: in `assistant.choose()`, check unsafe offers (callback, payment, change) before data words; overlap means
  silence. Plain identifier requests still get "Agent.".
Tests: the reviewer's N20, N21 and N22 tables inverted end to end, through a fake L2 that emits the contract labels
until L2 round 7 lands, then through the real detector after `git merge main`. Same LIMITS. DONE: tests, SHA.
