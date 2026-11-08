# Review R1b delta 27 (coordinator): one rule after a bot (L1 round 29)

PURPOSE: Your delta 26 (N23-R, N24, affirmative mismatch) blocked the Delta retry. L1 replaced the special cases with
one rule; this is the check before the retry.

TASK: Read-only review on `main` of L1 commit 8afa478 (`plugin/line/engine/`, new shared
`plugin/line/detect/affirmative.py` `clear_yes()`, `tests/l1/test_r29_uniform_rule.py`, `docs/lanes/L1.md` section
41) against `evidence/reviews/R1-DELTA26.md`. Rule: once a call had a bot label, the owner is rung only after our
fixed live-person question was asked and the reply fully matches the shared clear-yes list; the owner-ring call itself
refuses otherwise. L2's detector still uses its own table (switch-over pending); the engine rule is the gate. Re-run
your delta-26 tables, N23 and N20/N21 sequences, the controls and Delta run 1. The bot-says-yes residual is accepted.

LIMITS:
- Read only in the repo. Write only under `/Users/mac/.hermes/cache/scratch/hermes-line-r1d27/`.
- No network, no calls, no provider access. Read only `~/.hermes-line/approved_destinations.json` under that folder.
- Do not commit or push.

DONE: `REVIEW-DELTA27.md` there with findings and one verdict for the Delta retry: yes or no.
