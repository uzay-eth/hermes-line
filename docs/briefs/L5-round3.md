# L5 round 3 (coordinator)

First `git merge main` (main 2976a70: L1 round 5 wired your voice path; L1 R1-delta fixes; L4 N1 half).
Read `docs/lanes/L1.md` sections 11 and 12 ("Expectations for L5"). Update `tests/l5/` and, only where needed,
`plugin/line/voice/` to the new contract: `approval_generation` in OTP grant meta, `?g=N` on Connect actions,
per-generation media tokens `media-g{N}`, digits route nonce behaviour. Then all of `tests/l1`, `tests/l2`, `tests/l4`,
`tests/l5` must pass in your tree. Same LIMITS as your brief. DONE: four suite results, commit SHA.
