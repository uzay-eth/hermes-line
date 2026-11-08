# Review R1 delta 10 (coordinator): owner line mode (PIN) and round 12

Read only, same LIMITS and disposable-scratch rules, under `/Users/mac/.hermes/cache/scratch/hermes-line-r1d10/`.
Main 5b654a4. Diff `git diff 2faacc9 5b654a4 -- plugin/ tests/l1` (L1 commits 7f6d16d round 12, 29ea46e round 13;
`docs/lanes/L1.md` latest sections).
Focus on round 13, which is new authentication:
1. PIN: storage (`HERMES_LINE_OWNER_PIN`, optional_env), constant-time compare, DTMF and spoken entry, the 3-try
   limit (per call? per caller? across calls: can an attacker retry by calling again and again?), lockout and
   alerting, timing and transcript side channels, masking in transcript, events, Hermes, logs, evidence.
2. Caller ID: an owner-number caller without the PIN gets no owner power; spoofing; a stranger reaching the PIN prompt.
3. After the PIN: the spoken "yes" as approval: bound to one task and the read-back destination; can the callee of
   a later outbound leg, or audio injected on the line, produce that yes? Owner bridge of this leg.
4. Round 12: the owner greeting path, hints, briefing tool names, the restored owner-ring grant.
Write `REVIEW-DELTA10.md`. Verdict: an attended inbound trial where the owner calls the line, enters the PIN, asks
Hermes to call his own Fi line and bridge him in (all OTP off, ConversationRelay): yes or no.
