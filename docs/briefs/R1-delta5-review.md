# Review R1 delta 5 (coordinator)

Read only, same LIMITS and disposable-scratch rules, under `/Users/mac/.hermes/cache/scratch/hermes-line-r1d5/`.
Main now includes L1 commits 514c21a (N5), 89b76e2 (N4), 2e8bf75 (N6); diff `git diff 58a17b1 HEAD -- plugin/ tests/l1`.
`docs/lanes/L1.md` section 18 explains them; live evidence in `tests/l1/evidence/r9/`.
1. Re-run your three probes (`probe_rule12.py`, `probe_gate_gap.py`, the auxiliary-session fixture) against HEAD.
   Fixed / not fixed, file:line, do the new tests exercise them.
2. Check the new pieces: the `Enabled toolsets:` parser and the `hl_<8 hex>` identity binding (collision, spoofing,
   a profile toolset with a similar name), the empty MCP server for detector/summary sessions (does attaching it
   really freeze the session's tools in Hermes, `gateway/session_policy.py`), the reordered `_dial` (consent used
   once, cleanup on refusal, no dial after cancel).
Write `REVIEW-DELTA5.md`. Verdict, two parts, both OTP off, ConversationRelay, fallback ingress without Auth Token,
call profile `hermes-line-calls`: (a) second outbound call-only trial to the owner's Spectrum, (b) attended inbound
trial (Fi forwarded to the plugin line, owner ring on): yes or no each.
