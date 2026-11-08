# Review R1 delta 9 (coordinator): N11 only

Read only, same LIMITS and disposable-scratch rules, under `/Users/mac/.hermes/cache/scratch/hermes-line-r1d9/`.
L6 commit 2faacc9 (`docs/lanes/L6.md` round 7). Re-run `probe_delta8.py` and `probe_real_close_shutdown.py`
(adapt their assertions to the intended behaviour), and check the crash reconciliation path at the next preflight.
Write `REVIEW-DELTA9.md`. Verdict, as in delta 8: one outbound Grok call-only trial (max 3 min) to the owner's
Spectrum, then one attended inbound Grok trial via the Fi line, both OTP off: yes or no each.
