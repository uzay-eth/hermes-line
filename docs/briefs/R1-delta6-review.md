# Review R1 delta 6 (coordinator): N7 only

Read only, same LIMITS and disposable-scratch rules, under `/Users/mac/.hermes/cache/scratch/hermes-line-r1d6/`.
L1 commit b5ab5e2 fixes N7 (`docs/lanes/L1.md` section 19, `tests/l1/test_r10_n7.py`). Re-run your
`probe_cancel.py` (adapt its final assertion to the intended behaviour), check the stop-intent record for races
(stop arriving between the final check and the provider request, two processes, a tool trying to stop or un-stop),
and that "stopped before dialling" can never be reported for a call that was dialled. Write `REVIEW-DELTA6.md`.
Verdict for the second outbound call-only trial to the owner's Spectrum (same configuration as delta 5): yes or no.
