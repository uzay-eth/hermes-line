# L1 round 6 (coordinator): session-scoped call tools over ACP

Do this after the R1 delta 2 follow-up (N2, N1 residual), as its own commit.

L6 stopped (read `docs/lanes/L6.md` on main): with the ACP driver, in-call tools run inside the gateway's profile, which
cannot reach a plugin service running in another home (trial harness) or another store. Hermes ACP already accepts
per-session MCP servers: `acp_adapter/gateway_server.py:269-289` (`new_session(cwd, mcp_servers=...)`, McpServerHttp /
McpServerSse in `acp_adapter/server.py:20`). Verify that against the source and live behaviour, then:
1. The call service serves the in-call tools (`press_digits`, `request_finish`, `request_owner`, and whatever L5's voice
   path needs from Hermes) as a session-scoped MCP server on loopback only, with an unguessable per-session bearer
   token, bound to exactly one call session, revoked when the call ends. Same action restrictions, gates and consent
   as the existing in-call handlers; the existing handlers stay for the same-profile case.
2. `AcpDriver` passes that server in `session/new` (`mcpServers`), so a call works whether or not the plugin is
   installed in the gateway's own profile.
3. Tests: wrong/expired token, token of another call, after call end, non-loopback bind refused, tool parity with
   the in-process handlers, and one real `hermes acp` fixture-model run where the model calls `request_finish` through
   the session MCP (the gateway-attach rule from your live runs stays: never start a gateway; the fixture run may use a
   scratch gateway as in round 2).
Write the L6 integration contract (how the harness points the driver at the service) in `docs/lanes/L1.md`.
Same LIMITS as your brief. DONE: test output, commit SHA.
