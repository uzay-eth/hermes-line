# Review R1 delta 12 (coordinator): owner line N12, N14 residuals

Read only, same LIMITS and disposable-scratch rules, under `/Users/mac/.hermes/cache/scratch/hermes-line-r1d12/`.
Main HEAD, L1 commit 05e0257 (`docs/lanes/L1.md` section 24). N12 is now a keypad confirmation of the destination's
last four digits within 60 s; speech never approves. N14 is a rolling-window scrub with a 1.5 s hold. Re-run your
delta-11 probes (adapt assertions to the intended behaviour). Check: can a callee leg or injected audio produce the
confirmation digits (DTMF from which leg is accepted?), digits keyed before the read-back, the last-four collision
with another number, the documented residual (PIN pieces more than 1.5 s apart reach Hermes in part) and whether it
blocks the trial given keypad-only entry. Write `REVIEW-DELTA12.md`. Verdict: attended owner-line trial (owner calls
the line, keys the PIN, asks Hermes to call his Fi line, confirms by keypad, is bridged in; OTP off, Relay): yes or no.
