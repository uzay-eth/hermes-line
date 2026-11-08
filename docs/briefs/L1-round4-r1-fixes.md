# L1 round 4 (coordinator): fix R1 security findings

First `git merge main`. Read the review: `/Users/mac/.hermes/cache/scratch/hermes-line-r1/REVIEW.md` (read only, do not copy).
Fix in your paths, each with regression tests named after the finding:
- H2: key the REST verification cache by account plus the full leg expectation (direction, To, From); reject a
  conflicting expectation; evict entries. Inbound admission requires a current provider status that allows it and a
  persisted unique SID admission record; a Twilio retry of the same SID returns the existing result and creates no new
  session or authority. Verify the provider-returned SID. Tests: changed From after cache, completed-SID replay,
  replay after session end and after restart, concurrent admission.
- M2: process each Connect completion / recovery generation once; return the cached TwiML for retransmissions; debit
  the retry budget only on a real new recovery. Same for `/answer` minting relay tokens: one live token set per
  generation. Tests in both auth modes. Stop describing signatures as replay protection.
- M3: persist the owner's observed revision with each queued command and re-check it right before execution; stale =
  refused with a stale result. Test: pause the consumer, change state, resume.
- M1 (your half): carry the exact resolved destination (call id + counterpart) in the server-owned pending OTP
  approval and compare it, plus liveness, at execution; changed = refuse, new prompt needed. L4 binds its slot to the
  same approval generation. Use
  `approval_generation: int` on the pending approval and pass it to L4's release call.
- Signature-mode downgrade: if a configured Auth Token cannot be read, fail closed and say so; do not fall back.
- Update the "does not protect" list in `docs/lanes/L1.md` with the reviewer's additions.
Same LIMITS as your brief. DONE: `tests/l1/` output, commit SHA, one line per finding with the test names.
