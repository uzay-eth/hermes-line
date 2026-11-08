# L4 round 3 (coordinator): integration fixes after L1 merged

First run `git merge main` in your worktree (L1 is merged). Read `docs/lanes/L1.md` section 5. Fix, in your paths only:
1. `mask_for_transcript` over-masks call speech: "Friday the 14th", "confirmation number 48213", "775 555 0101" and
   "DL1234" become `[CODE]`, and a card becomes `[CODE]` instead of `[CARD]`. Call transcripts must stay readable;
   mask verification codes as `[CODE]`, card-like runs as `[CARD]`, phone numbers as `[NUMBER]` (contract mask
   tokens). A booking or confirmation number the business reads out is not an OTP: keep it, unless it equals a code
   the plugin received by SMS (L1 already uses your exact-value `scrub_text` for that). Add these cases as tests.
2. Absolute `line.*` imports in `released.default_store()` and `engine_stub.resolve_send_secret_to_call()` do not
   resolve under Hermes's plugin package name. Use package-relative imports.
3. `tests/l4/test_service.py::test_default_store_is_l1s_module_when_present_else_memory` now fails because L1's module
   exists. Update it to the new truth and write the reason in the commit.
4. `hermes plugins validate` flags invisible Unicode in `line/sms/textnorm.py:14-16`. Write those characters as
   `\u` escapes so the source has no invisible characters; behaviour unchanged.
Same LIMITS as your brief. DONE: `tests/l4/` and `tests/l1/` both pass in your tree (paste counts); commit SHA.
