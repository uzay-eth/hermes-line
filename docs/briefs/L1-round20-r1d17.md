# L1 round 20 (coordinator): R1b delta 17 (N16-D, N16-E, N17-T)

`git merge main`. Read `evidence/reviews/R1-DELTA17.md` and the scripts under
`/Users/mac/.hermes/cache/scratch/hermes-line-r1d17/` (read only; copy the added cases into tests/l1).
Decisions (restricted and information-only tasks):
- Whole-menu scope: normalise the CURRENT menu text as one string (lower case, all punctuation and line breaks to
  single spaces, so split expressions rejoin). If any blocked word or phrase, or any callback offer, appears anywhere
  in it, send nothing at all for that menu, not even "agent"; stay silent and wait for the next menu.
- Positive form only: an option qualifies only in the forms "for <safe topic>", "to speak / talk to / with an agent or
  representative", or "<safe topic>, press N" where safe topic is exactly reservations, baggage, general information,
  agent, representative. Any other verb in the option ("to buy", "to bill", "to put down") disqualifies it.
- Add buy, bill, deposit, sell, order, renew, subscribe, transfer, reserve to the blocked words; keep the rest.
- N17-T: keep the turn context available to reject an in-flight tool after a timeout; a timed-out turn's tools
  refuse.
Tests: the reviewer's added cases inverted, plus the original matrix. Same LIMITS. DONE: tests, SHA.
