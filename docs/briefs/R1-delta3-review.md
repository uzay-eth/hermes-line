# Review R1 delta 3 (coordinator)

Read only, same LIMITS and disposable-scratch rules as before, now under
`/Users/mac/.hermes/cache/scratch/hermes-line-r1d3/`. Main is at 1d16bfd.
1. N2 and the N1 residual: re-run your reproductions. Fixed / not fixed with file:line; do the new tests exercise them.
   (L1 commit 6a02b36, L4 commit 890a4f1.)
2. New: the session-scoped MCP endpoint for in-call tools (L1 commit c5f721e; `docs/lanes/L1.md` sections 13-14):
   loopback-only bind, Host header check, per-call bearer token (hash stored, revoked at end), binding to one call,
   tool parity with the in-process handlers, what the Hermes gateway stores about the token
   (`acp_adapter/gateway_server.py` around 269-289, read only). Can another local process, another call, or a page in
   a local browser (DNS rebinding, CSRF to 127.0.0.1) drive these tools? Can a tool reach a dial, owner ring, DTMF or
   OTP outside the existing gates?
Write `REVIEW-DELTA3.md`. Verdict for the same call-only ConversationRelay trial (OTP off), now with this endpoint in
the path: yes or no.
