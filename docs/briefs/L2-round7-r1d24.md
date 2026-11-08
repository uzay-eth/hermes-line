# L2 round 7 (coordinator): R1b delta 24 N20, N21 (detector side)

`git merge main`. Read `evidence/reviews/R1-DELTA24.md` (N20, N21) and the scripts under
`/Users/mac/.hermes/cache/scratch/hermes-line-r1d24/` (read only; copy cases into tests/l2).
Contract (L1 round 27 builds on it at the same time; keep exactly these labels):
- A personal name alone never overrides an explicit bot declaration made earlier in the call unless a boundary came
  in between. Boundary = a transfer line, hold_music, a hold message, or 8 s or more with no business speech.
  Without a boundary: a named greeting after a declared bot is `unknown` (low) with evidence `possible_new_party`.
- After a boundary: a named greeting stays `human` (high) as in round 6; an unnamed greeting or question ("Hello, how
  can I help?", "May I have your confirmation number?") is `unknown` with `possible_new_party`, never
  `automated_assistant`, unless the line itself declares automation or matches a bot-only pattern.
- Without a boundary, a line with human-only cues after a bot ("Hello? Are you there?", "Sorry, can you hear me?")
  is also `unknown` with `possible_new_party`.
- Transfer lines are `hold_message` with evidence `transfer`. Widen them: "please wait while I connect you",
  "connecting you now", "one moment while I transfer you", "I'll get you to someone", "putting you through",
  "let me get an agent", plus round 6's forms. L2 is the single source of transfer recognition; L1 will drop its own.
Targets: the reviewer's N20 and N21 sequences, 0 false humans on bot fixtures, 0 bot labels on agent lines after a
boundary. Same LIMITS, offline only. DONE: tests, counts, SHA.
