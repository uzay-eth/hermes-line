# L6 round 2 (coordinator): resume

`git merge main` (main 1d16bfd). The missing hook is built: L1 round 6 serves in-call tools as a session-scoped
MCP endpoint and `AcpDriver` passes it in `session/new`. Read `docs/lanes/L1.md` section 14 (L6 integration contract)
and section 13. Delete your own blocker note's "not ready" status once the harness works, keep the history.
Build the harness exactly as your original brief says (`docs/briefs/L6-trial-harness.md`), same LIMITS: you may install
cloudflared, start and stop the quick tunnel, read the Twilio balance in `preflight`, and run `dry`. Do not run
`call`. Attach to the owner's running gateway only for `dry`'s ACP check if your brief's attach rule allows it; one
saved session in the owner's profile is acceptable, titled `hermes-line trial dry`.
Note from L1: the MCP tools are deferred behind Hermes tool search (one extra model step before first use). Measure
that step's time in `dry` if it needs no extra call.
DONE: `preflight` and `dry` outputs (masked) in `docs/lanes/L6.md`, tunnel stopped, `tests/l6/` output, commit SHA,
the exact first-call command.
