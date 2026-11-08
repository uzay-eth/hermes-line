# Review R1b delta 26 (coordinator): clear yes only after a bot (L2 round 8, L1 round 28)

PURPOSE: Your delta 25 N23 blocked the Delta retry. Both layers answered; this is the last check before the retry.

TASK: Read-only review on `main` of L2 commit 30859cb (`plugin/line/detect/`, `tests/l2/`, `docs/lanes/L2.md`
round 8) and L1 commit 7ad61a6 (`plugin/line/engine/`, `tests/l1/test_r28_clear_yes.py`, `docs/lanes/L1.md` section
40) against `evidence/reviews/R1-DELTA25.md`. Rule: once a call had a bot label, only a clear yes to our live-person
question rings the owner; silence alone is no longer a boundary for a named greeting. Re-run your N23 table, the
10 s named-bot case, the controls, and Delta run 1. The bot-says-yes residual is accepted (owner must press 1).

LIMITS:
- Read only in the repo. Write only under `/Users/mac/.hermes/cache/scratch/hermes-line-r1d26/`.
- No network, no calls, no provider access. Read only `~/.hermes-line/approved_destinations.json` under that folder.
- Do not commit or push.

DONE: `REVIEW-DELTA26.md` there with findings and one verdict for the Delta retry: yes or no.
