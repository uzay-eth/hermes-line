# L4 round 4 (coordinator): fix R1 security findings

First `git merge main`. Read the review: `/Users/mac/.hermes/cache/scratch/hermes-line-r1/REVIEW.md` (read only).
Fix in your paths, each with regression tests named after the finding:
- H1: bind every sealed slot to an immutable approval generation plus the full sender/window/destination binding.
  Invalidate the slot on replacement or revocation (`registry.forget` / re-register) and reject any mismatch before
  either sink. Serialize validation and delivery against registry replacement. Tests: fill->fill, fill->call,
  call->call replacement, concurrent replacement, retry with no token after replacement.
- M1 (your half): accept `approval_generation: int` from L1's pending approval in the release call and refuse when it
  differs from the slot's generation.
- Browser fill: re-probe the top-level origin immediately before typing and again right after; on any change, stop
  and report `origin_changed`. Add a setting `otp_browser_fill` default OFF; the call target stays on. Write the
  remaining race and the frame-origin limit in `docs/lanes/L4.md` as open residuals.
Same LIMITS as your brief. DONE: `tests/l4/` output (and `tests/l1/` still passing in your tree), commit SHA, one
line per finding with the test names.
