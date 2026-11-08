# L1 round 8 (coordinator, queued: send after round 7)

From L7's admission check (`docs/release/admission-check.md`, merged on main). Coordinator decisions included.
1. G5 (catalog rule 12): the call's Hermes session must not run with the profile's full toolset and approval mode,
   because a stranger on the phone partly drives it. Restrict the ACP call session to the session-MCP call tools only
   (no terminal, file, browser, web or other MCP tools), using what Hermes ACP supports (read
   `acp_adapter/` for per-session toolset/approval controls; verify live with the fixture-model run). If Hermes has no
   way to do it, stop and report the exact missing hook and the options.
2. G3 + decision: browser code fill is removed from the catalog build. Drop `line_otp_fill` from `plugin.yaml`,
   registration, skill and docs; keep `line_otp_to_call`. L4's library code may stay but must be unreachable.
3. G7: tool descriptions and the skill must match what exists (no "approve in the Desktop pane" unless the pane has
   the button; document `line_otp_to_call`).
4. Done by L2 (f61fa04) and the coordinator (trial config passes `policy.blocked_destinations`). Only check that
   EngineConfig validation and the Desktop settings surface pass the list through, with a test. Original item:
   the Bora number leaves the public code (`destinations.py`). Add a user setting `blocked_destinations`
   (default empty); the trial harness config sets it for this machine. Coordinate with L2 if the file is theirs:
   L2 owns `plugin/line/policy`, so put only the config plumbing in your paths and tell the coordinator the exact
   policy change for L2.
5. G6/N3 (Low, R1 delta 3): a request already authenticated must not survive token rotation; re-check the token
   after reading the body.
6. G2: add `plugin/README.md` (copy of the root README with image links that work from `plugin/`; the coordinator pins
   them to the release commit later).
Same LIMITS. Validate cold at the end. DONE: test output, validate output, SHA.
