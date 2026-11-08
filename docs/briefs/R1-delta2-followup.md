# R1 delta 2 follow-up (coordinator)

Read `evidence/reviews/R1-DELTA2-2976a70.md` (on main). Verdict was YES for the call-only trial; fix these before
OTP or Media Streams go live.

## L1
- N2: the digits route must check call state before honouring a valid nonce. In owner hand-off / owner_connected (or
  any state where the menu gate refuses presses) return the state-preserving TwiML (`_preserving_twiml`), consume the
  nonce, play nothing, open no stream, change no generation. Test in both auth modes: nonce issued on hold, owner
  takes over, delayed digits webhook arrives.
- N1 residual (your half): `send_secret_to_call` refuses a missing `approval_generation` instead of adopting the
  current one.
- Extend the "does not protect" list in `docs/lanes/L1.md` with the reviewer's Media Streams wording.

## L4
- N1 residual (your half): make `approval_generation` mandatory at the call sink boundary; refuse sinks that cannot
  accept it. Update the legacy-sink test to expect refusal and add the replacement interleaving.
