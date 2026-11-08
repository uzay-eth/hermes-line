# L1 round 18 (coordinator): R1 delta 13 N16, N17

`git merge main`. Read `evidence/reviews/R1-DELTA13.md` and `fixtures/probe_hold.py` under
`/Users/mac/.hermes/cache/scratch/hermes-line-r1d13/` (read only; copy into tests/l1).
- N16: one final output gate for restricted tasks, applied to BOTH spoken menu replies and DTMF, at the moment of
  sending: allow only positively identified safe actions (navigation to reservations/baggage/general info, stay in
  queue / decline / "no", "agent"/"representative"). Anything that could accept a callback, a charge, a payment, a
  change, or is ambiguous fails closed (send nothing, log `restricted_action_blocked`). On a callback offer never
  send "yes" or the accept key, whatever the vocabulary says. Map the key to its menu option from the menu text;
  if the key's option is unknown, block. Widen the payment/change wording list (authorize, charge, card, fee
  payment, purchase, upgrade, rebook, cancel).
- N17: on entering the human / fixed-script phase, bump the menu generation and cancel pending menu turns; at
  every speech and tool dispatch require the originating turn's generation and phase to still be current, so a
  late worker sends nothing.
Tests: the three probe cases inverted, plus delayed menu reply crossing human detection, owner ring, fallback and
acceptance (no stale speech or tool effect in each).
Same LIMITS. DONE: tests, SHA.
