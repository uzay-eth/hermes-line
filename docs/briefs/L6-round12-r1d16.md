# L6 round 12 (coordinator): R1b delta 16 N18 residual

`git merge main`. Read `evidence/reviews/R1-DELTA16.md` and `fixtures/review_probes.py` under
`/Users/mac/.hermes/cache/scratch/hermes-line-r1d16/` (read only; copy the case into tests/l6).
Fix as the review says: an unresolved create is resolved only by evidence that identifies THAT create (a call whose
DateCreated is at or after the journaled attempt time minus 2 s, same To/From/role, including queued, ringing and
in-progress states, with no upper time bound) and confirms it ended; end every live candidate found. A different
older call never resolves it. If identity cannot be established, keep the startup block and add
`tools/trial/run.py reconcile --i-am-the-coordinator`, which lists the candidates (masked) and clears the record
only after it has confirmed none of them is live. Tests: the reviewer's collision case, a queued call past the old
window, two candidates. No live run. LIMITS as before. DONE: tests, SHA.
