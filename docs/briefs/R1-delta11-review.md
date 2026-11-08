# Review R1 delta 11 (coordinator): owner line fixes N12-N15

Read only, same LIMITS and disposable-scratch rules, under `/Users/mac/.hermes/cache/scratch/hermes-line-r1d11/`.
Main HEAD. L1 commits 76b45e1 (N15), 3e9671b (N14), 96e0f79 (N13), d3fc6ef (N12); `docs/lanes/L1.md` section 23.
Re-run your delta-10 probes (adapt assertions to the intended behaviour). Check especially: the N12 arming estimate
from text length (can a yes land before the read-back is audible?), the persistent attempt store (races between two
concurrent calls, clock changes, unlock path owner-only), the `[PIN]` scrub at every boundary, and that a locked
line offers nothing. Write `REVIEW-DELTA11.md`. Verdict: an attended inbound trial where the owner calls the line,
keys the PIN, asks Hermes to call his own Fi line and bridges in (OTP off, ConversationRelay): yes or no.
