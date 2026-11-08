# Review R1b delta 20 (coordinator): leading-text rule (L1 round 21)

PURPOSE: Final engine check before one supervised airline hold test. The author answered your delta-19 finding.

TASK: Read-only review on `main` of L1 commits 037077e and e90fbe4 (`plugin/line/engine/menu_gate.py`,
`tests/l1/test_r21_leading_text.py`, `tests/l1/r1d19/`, `docs/lanes/L1.md` sections 33-34) against
`evidence/reviews/R1-DELTA19.md`. Re-run your delta-19 and delta-17 cases. Check the intro allowlist and its
exempt wording (can a name or an exempt phrase carry a payment or callback meaning), the amount rule, and the
PIN-test change in e90fbe4 (does it still catch a planted PIN).

LIMITS:
- Read only in the repo. Write only under `/Users/mac/.hermes/cache/scratch/hermes-line-r1d20/`.
- No network, no calls, no provider access. Do not read `~/.hermes-line/`.
- Do not commit or push.

DONE: `REVIEW-DELTA20.md` there with findings and one verdict for the airline hold test (engine and harness): yes or no.
