# L6 round 5 (coordinator): dry fails after L1 round 9

`git merge main` (3c9eccb: L1 r9 changed the rule-12 guard: bridge-only `/tools` replies are refused, only the
`Enabled toolsets:` form holding this call's own `hl_<8 hex>` server passes; detector/summary sessions get an empty
MCP server; `_dial` reordered. Read `docs/lanes/L1.md` section 18 and `tests/l1/evidence/r9/`).
On main, `preflight` passes and `dry --caller-id fi` fails with `ACP session failed to attach: AcpError`.
L1's live run with the same tool settings passed, so find the difference. Theories to test in parallel, at least:
the real `hermes-line-calls` profile still shows the bridge form (tool search setting, deferred tools) while L1's
scratch copy did not; the harness `Driver` subclass or `probe()` uses names or a flow L1 changed (`BRIDGE_TOOLS`,
server name, `session_tools_reply`); the harness does not pass the new per-call server tag; a Hermes 64-char tool
name limit with the harness's names. Fix in `tools/trial/` only. If the profile itself must change, do not edit it:
report the exact change. If the plugin must change, report the exact hook.
Then run `preflight` and `dry --caller-id fi` once more (no call). LIMITS as before.
DONE: theories ruled out and how, masked dry output, tests, SHA.
