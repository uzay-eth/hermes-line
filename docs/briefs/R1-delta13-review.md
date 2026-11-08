# Review R1 delta 13 (coordinator): hold trial to a real airline line

Read only, same LIMITS and disposable-scratch rules, under `/Users/mac/.hermes/cache/scratch/hermes-line-r1d13/`.
Main HEAD. L1 commits eb747b7 (round 16: no owner ring on assumption, enforced task rules, seven-digit confirm),
6c6b16d (round 17: opt-in live-leg owner bridge), 0764706 (carrier screening press-1, one note offer, owner-timeout
fallback question with fixed lines); `docs/lanes/L1.md` latest sections; `docs/lanes/L2-hold-trial-plan.md`.
L6 round 10 (hold command) will follow; review it as a delta when it lands.
The trial: one ConversationRelay call to Delta reservations +1 800-221-1212 (owner approved), menu, up to 45 min hold,
on a human: brief greeting, ring the owner's Spectrum; owner accepts -> bridge; no accept in 20 s -> one fixed
question (checked-bag fee), thanks, end. Never invent a booking or identity, no callback, payment or change.
Check: can hold audio, announcements or a callback offer ring the owner or press a forbidden key; can the agent (a
stranger) steer Hermes into speech or tools beyond fixed lines on this task; the inbound screening press-1 (could a
caller abuse it); the live-leg bridge opt-in default off; spend and time bounds on long holds.
Write `REVIEW-DELTA13.md`. Verdict for the Delta hold trial (engine side): yes or no.
