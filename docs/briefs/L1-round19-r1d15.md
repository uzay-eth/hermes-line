# L1 round 19 (coordinator): R1 delta 15 (N16-A, N16-B, N16-C, N17-R)

`git merge main`. Read `evidence/reviews/R1-DELTA15.md` and the scripts under
`/Users/mac/.hermes/cache/scratch/hermes-line-r1d15/` (read only; copy the case matrix into tests/l1).
Parsing menu meaning keeps leaking, so make the gate smaller instead of smarter. Decisions:
- Information-only tasks (the Delta trial): the only outputs allowed during menus are the spoken words "agent" or
  "representative", "no" only as an answer to a callback offer that the menu sentence says keeps you on the line
  with no later call (if unsure, block), and ONE single key per menu, only when the sentence announcing that key
  holds exactly one option, that option is on a short positive list (reservations, baggage, general information,
  speak to an agent or representative), and the sentence has no conjunction joining a second clause ("and", "or",
  "then", "to ... and ...") and no blocked word anywhere (call, phone call, call back, callback, later, place in
  line, hang up, text, charge, pay, payment, card, authorize, purchase, upgrade, fee, change, rebook, cancel,
  modify, confirm, accept, yes). Never "yes". No sequences. Anything else: send nothing, log
  `restricted_action_blocked`. If the menu offers nothing allowed, stay silent and wait.
- Restricted (non information-only) tasks: same blocked words and one-option rule; one key per current menu; a
  multi-key sequence only if the menu announces that exact sequence as one option.
- N17-R: capture the phase epoch when a turn is scheduled; re-check it after awaiting the previous turn and at every
  speech and tool dispatch; entering the human phase invalidates queued turns as well as the active one.
- C1 (Low): accept literal `*` as the star key; keep fail-closed.
Tests: every case in the reviewer's matrix, inverted where it was a finding; the queued turn B case.
Same LIMITS. DONE: tests, SHA.
