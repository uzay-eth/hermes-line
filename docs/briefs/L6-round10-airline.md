# L6 round 10 (coordinator, queue after round 9): airline hold trial scope

After round 9 is committed: read `docs/lanes/L2-hold-trial-plan.md` (preconditions 3, 4, deadline and balance rules).
Add a purpose-specific `hold` trial command: one business leg to a destination given by `--business` that must also
appear in `~/.hermes-line/approved_destinations.json` (written by the coordinator only after owner approval; each
entry has number, label, approved_by, expires), plus the owner leg (Spectrum) for the bridge; role-aware checks, no
retries, no redial. 45-minute global wall-clock cap from the first create, provider TimeLimit matching the remaining
time on each leg. Ongoing budget guard: poll balance every 60 s (read-only) and keep a conservative running estimate
(business + Relay + owner + conference minutes at current rates read from Twilio Pricing API if allowed by the key,
else the S2 planning rates x 1.5); stop all legs when projected balance reaches $4.00 or the global cap arrives.
Full cleanup of both legs and the conference on every exit. Evidence: menu time, hold time, detection latency,
false-human count, bridge time, cost. Tests offline with the fake adapter, and a `dry` form of the hold command.
No live call. LIMITS as before. DONE: tests, SHA, exact command.
