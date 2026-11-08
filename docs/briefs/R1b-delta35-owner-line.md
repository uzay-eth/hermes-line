# Review R1b delta 35 (coordinator): owner line on current main, one round

PURPOSE: Your delta 12 said YES for one attended owner-line trial, but the engine changed a lot since (L1 rounds
16-36). Can will key his PIN and ask Hermes to call his own Fi line. One round only.

TASK: Read-only review on `main` of the owner-line path (`plugin/line/engine/` owner line, PIN, keypad confirmation,
`tools/trial/run.py listen`, `tests/l1/test_r13*`, `test_r14*`, `test_r15*`, `tests/l6/` owner rehearsal, L6 commit
117d6b0). Re-run your delta-10 to delta-12 probes on current main. Blocking findings are only Critical/High, or any
path that rings or dials wrongly in a way that leaks data (PIN, codes) or commits to something. Put Medium/Low in a
separate list for the backlog.

LIMITS:
- Read only in the repo. Write only under `/Users/mac/.hermes/cache/scratch/hermes-line-r1d35/`.
- No network, no calls, no provider access. Do not read `~/.hermes-line/` except `approved_destinations.json`.
- Do not commit or push.

DONE: `REVIEW-DELTA35.md` there with blocking findings, backlog list, and one verdict for the owner-line trial.
