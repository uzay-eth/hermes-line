# L5 round 4 (coordinator)

`git merge main` (main 1d16bfd: L1 N2 fix, N1 residual — `send_secret_to_call` now REFUSES a missing or non-int
`approval_generation` —, L4 makes the generation mandatory at the call sink, L1 round 6 session MCP).
On main, `tests/l5/test_l1_rehearsal.py::test_verification_code_needs_the_owner_grant_and_never_reaches_the_model`
fails again with `no code was approved for this call`. Diagnose first: is it the fixture (e.g. a hard-coded
generation that no longer matches L4's registry), or does the voice path's `send_verification_code` / media sealed
send not pass the approval generation through to L1? If it is the voice path, it is a product bug: fix it in
`plugin/line/voice/` so the Grok tool carries the generation end to end, with a test. If only the fixture, fix the
fixture and say why. All four suites must pass in your tree. Same LIMITS. DONE: diagnosis in one line, suite results,
commit SHA.
