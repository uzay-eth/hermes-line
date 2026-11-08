# Review R1b delta 21 (coordinator): approved intro names (L1 round 22, L6 round 14)

PURPOSE: Final check before one supervised hold test on Delta reservations. The author answered your delta-20 findings.

TASK: Read-only review on `main` of L1 commit b32ca11 (`plugin/line/engine/menu_gate.py`, task option
`intro_names`, `tests/l1/test_r22_intro_names.py`, `docs/lanes/L1.md` section 35) against
`evidence/reviews/R1-DELTA20.md`, and L6 commit b137b1b (`tools/trial/hold.py`: intro names only from the approval
row, missing means empty). Re-run your delta-20, 19 and 17 cases with the approved names the live run will use
("Delta", "Delta Air Lines"; you may read them in `~/.hermes-line/approved_destinations.json`).

LIMITS:
- Read only in the repo. Write only under `/Users/mac/.hermes/cache/scratch/hermes-line-r1d21/`.
- No network, no calls, no provider access. Read only `~/.hermes-line/approved_destinations.json` under that folder.
- Do not commit or push.

DONE: `REVIEW-DELTA21.md` there with findings and one verdict for the Delta hold test (engine and harness): yes or no.
