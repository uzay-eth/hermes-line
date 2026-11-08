# Review R1 delta 8 (coordinator): N9, N10

Read only, same LIMITS and disposable-scratch rules, under `/Users/mac/.hermes/cache/scratch/hermes-line-r1d8/`.
L5 commit 07263e1 (`docs/lanes/L5.md` section 6, `tests/l5/test_review_r1d7.py`). Re-run your delta-7 probes,
check the new connect helper, setup tasks and private logger for regressions (stream reader not cancelled, sockets
closed on every exit, key in no log or exception). Also: L6 commit 91c0152 adds `--transport grok` to
`tools/trial/run.py` and an xAI spend ledger; check how the key is loaded there (file -> secret scope) and that the
ledger cap is enforced before a run. Write `REVIEW-DELTA8.md`. Verdict: one outbound Grok call-only trial (max 3 min)
to the owner's Spectrum, then one attended inbound Grok trial via the Fi line, both OTP off: yes or no each.
