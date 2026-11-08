# L1 round 16 (coordinator): blockers for the real hold-line trial, plus delta-12 Mediums

`git merge main`. Read `docs/lanes/L2-hold-trial-plan.md` (preconditions 2 and 5) and
`evidence/reviews/R1-DELTA12.md` (the two Medium limitations).
1. `_maybe_confirm` promotes two unclear replies to `human_assumed` and may ring the owner. In owner mode (and in any
   call while L2 says hold/IVR/UNKNOWN), never ring the owner on an assumption: only an L2 HUMAN verdict, or a clear
   affirmative to the live-person question, rings. Agent mode may keep talking as today. Align the engine's `_words`
   artifact filter with L2's known hold artifacts so hold music junk gets no confirmation speech.
2. Task restrictions must be enforced, not only in text: a task's guidance "decline callback offers / no payment /
   no flight change" becomes a per-task deny list the call tools check (e.g. `press_keys` refuses the callback-offer
   digit when the last L2 detection is a callback offer and the task forbids callbacks; `report_outcome` cannot
   report a change as done). Pass L2 `Detection.evidence` to Hermes with the menu text.
3. Delta-12 Medium, suffix collision: if the proposed destination is not the owner's own number or a number
   already used in a previous approved task, require the last seven digits instead of four. Medium, late PIN
   fragments: keep as documented residual; add it to the README limits.
Same LIMITS. DONE: tests (hold junk never rings in owner mode, callback digit refused, seven-digit rule), SHA.
