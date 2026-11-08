# Review R1b delta 17 (coordinator): smaller menu gate (L1 round 19)

PURPOSE: Same airline hold test as delta 15. The author replaced menu interpretation with a small allowlist; confirm
it meets the spec and closes your delta-15 findings.

TASK: Read-only conformance review of L1 commit e7fd63d on `main` (`plugin/line/engine/menu_gate.py`,
`plugin/line/engine/machine.py` phase handling, `tests/l1/test_r19_small_gate.py`, `tests/l1/conformance_r1d15.py`,
`docs/lanes/L1.md` sections 30-31) against `evidence/reviews/R1-DELTA15.md`. Re-run your case matrix and extend it
where the new rules have edges (punctuation instead of conjunctions, two sentences that share a key, blocked words
split across sentences, a phase change while a tool call is in flight). The 4 safe inputs it now refuses are by
design.

LIMITS:
- Read only in the repo. Write only under `/Users/mac/.hermes/cache/scratch/hermes-line-r1d17/`.
- No network, no calls, no provider access. Do not read `~/.hermes-line/`.
- Do not commit or push.

DONE: `REVIEW-DELTA17.md` there with the case results, findings and a verdict for the airline hold test, engine side:
yes or no.
