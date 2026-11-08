# L1 round 15 (coordinator): R1 delta 11 residuals N12, N14

`git merge main`. Read `evidence/reviews/R1-DELTA11.md` and its probes under
`/Users/mac/.hermes/cache/scratch/hermes-line-r1d11/fixtures/` (read only). Timing cannot prove the read-back was
heard (Relay reports no playback end), so stop relying on it. Decisions:
- N12: replace the spoken yes with a keypad confirmation bound to the destination. The read-back ends with "To
  confirm, key the last four digits of the number, then pound." Approval = the keyed digits equal the last four of
  the proposed destination, for that task and generation, within 60 s; wrong digits or a timeout cancel (task stays
  awaiting approval, chat approval still works). Speech never approves on the owner line. Remove the timer arming.
  Toll-free or short numbers: same rule with the last four digits.
- N14: the PIN is keypad only, so caller speech that could carry it is scrubbed with a rolling window: keep the
  digit sequence (words and numerals, any separators) of the last few caller frames; when the PIN appears in that
  window, replace every contributing fragment with `[PIN]` in the transcript, detector input and Hermes history
  (redact retroactively in the store row if a fragment was already written; Hermes text already sent cannot be
  recalled, so hold caller frames that end in an unfinished digit run for up to 1.5 s before sending). Cover the
  reviewer's split and semicolon cases.
Same LIMITS. DONE: tests (both residual probes inverted), SHA, README step 8 updated.
