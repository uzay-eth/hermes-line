# Review R1b delta 31 (coordinator): provisional partials and one question counter (L1 round 33)

PURPOSE: Your delta 30 (N30, N31) blocked Delta run 3. L1 answered; this is the check before asking the owner to go.

TASK: Read-only review on `main` of L1 commit e772808 (`plugin/line/engine/machine.py`,
`tests/l1/test_r33_provisional.py`, `tests/l1/test_r32_slots.py`, `docs/lanes/L1.md` section 45) against
`evidence/reviews/R1-DELTA30.md`. Re-run your delta-30, 29 and 28 probes and Delta runs 1 and 2. Note the author's
scoping: promotion only on outbound calls before a person; the global 4-question budget only on strict calls
(restricted, information-only, or after a bot label). The Delta run is a strict call.

LIMITS:
- Read only in the repo. Write only under `/Users/mac/.hermes/cache/scratch/hermes-line-r1d31/`.
- No network, no calls, no provider access. Read only `~/.hermes-line/approved_destinations.json` under that folder.
- Do not commit or push.

DONE: `REVIEW-DELTA31.md` there with findings and one verdict for Delta run 3: yes or no.
