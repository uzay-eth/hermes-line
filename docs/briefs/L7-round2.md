# L7 round 2 (coordinator)

`git merge main` (58a17b1). L1 round 8 added `plugin/README.md` with this round's corrections (restricted call
profile, rule-12 disclosure, no browser fill, new settings `call_profile`, `forwarding_number`,
`inbound_ring_owner`, `reply_budget_s`, `blocked_destinations`, toll-free allowed, the silence bug fixed offline).
1. Make the root `README.md` match `plugin/README.md` in content (the root may keep repo-level sections: layout,
   development, tests). One source of truth for user-facing text; say which one in a comment at the top.
2. Update `docs/release/admission-check.md`, `catalog-entry.yaml` and `PR.md` for the closed gaps (G2, G3, G5, G6,
   G7 per L1 round 8; toll-free per L2 round 3) and the decisions: category `voice`; browser fill removed; Bora
   number moved to `blocked_destinations`. G1 (public repo) and G8 (license, author) stay open as owner steps.
3. Storyboard: no toll-free limit any more; keep your recommendation A for the business role and the recorded-call
   disclosure line for the shoot.
Same LIMITS (paths now also `plugin/README.md` only if the two must differ in a link). DONE: SHA, remaining gaps.
