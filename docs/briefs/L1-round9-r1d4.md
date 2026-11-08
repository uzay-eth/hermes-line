# L1 round 9 (coordinator): R1 delta 4 findings

Read `evidence/reviews/R1-DELTA4-58a17b1.md` (on main) and the reviewer's probes under
`/Users/mac/.hermes/cache/scratch/hermes-line-r1d4/` (read only; copy a probe into `tests/l1/` if useful).
Verdict was NO for both trials because of N4 (High), N5 and N6 (Medium). Fix all three, one commit each.

- **N4:** the guard must not trust the three bridge names. Check the complete executable catalog of the call
  session, not the displayed list: find what Hermes exposes for that (for example `tool_search` with a catch-all
  query listing every deferred tool, a gateway inventory call, or the profile's own config read by the plugin:
  `platform_toolsets.acp`, `mcp_servers`, enabled plugins, portable MCP). Fail closed when the full scope cannot be
  verified. Bind the session MCP identity to this call's exact server tag. Tests must include an unrelated tool
  hidden behind the bridge (the reviewer's `probe_rule12.py` case) and pass the live restricted profile
  `hermes-line-calls` on this Mac (fixture model, scratch run as before; do not edit that profile; it now has
  `mcp_servers: {}`).
- **N5:** start and check the agent first, then re-check call liveness and run `gate.authorize` (destination,
  quiet hours, consent still valid) immediately before dispatch. Do not consume consent twice or mint new consent.
  Clean up the provisional session on refusal. Tests: the reviewer's two probes (quiet-hours boundary crossed during
  startup, destination blocked during startup) dial nothing.
- **N6:** detector and summary sessions get the same full-catalog check before any caller text is sent, with a frozen
  empty toolset if Hermes allows it. Test with profile drift between the primary and the auxiliary session.
If a fix needs a Hermes change, stop at that point and report the exact hook and options.
Same LIMITS. DONE: test output, SHAs, live restricted-profile evidence.
