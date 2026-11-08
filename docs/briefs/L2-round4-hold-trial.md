# L2 round 4 (coordinator): readiness for a real hold-line trial

`git merge main`. Next live trial (pending owner approval of the number): one outbound ConversationRelay call to a
real airline reservations line (candidate: Delta reservations, +1 800-221-1212), navigate its menu, hold up to 45
minutes, detect the live agent, then ring the owner and bridge (owner mode). Prepare so that the call succeeds the
first time:
1. Hold on ConversationRelay: what Relay sends during hold music and repeated hold announcements (empty prompts,
   junk words, nothing); make sure the detector never calls a human on music, announcements or "your call is
   important", and still catches "Hi, this is <name> with Delta, how can I help?" within 2 s. Extend hold-sim fixtures
   with realistic airline hold transcripts (recorded-style lines, IVR prompts asking for a confirmation number or
   SkyMiles number, callback offers "press 1 to get a call back" which must be declined unless the task says so).
2. Menu navigation: the agent's guidance for "reservations / change a flight" without a confirmation number; DTMF
   versus speech; when to say "agent" or "representative".
3. Write `docs/lanes/L2-hold-trial-plan.md`: expected phases, what is measured (menu time, hold time, detection
   latency, false-human count, bridge time), stop rules (45 min cap, balance stop line), and what counts as pass.
Same LIMITS; offline only, no network. DONE: tests, SHA, the plan file.
