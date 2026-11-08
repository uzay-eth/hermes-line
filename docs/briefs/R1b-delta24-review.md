# Review R1b delta 24 (coordinator): speaking to automated assistants (L1 round 25, L2 rounds 5-6)

PURPOSE: Delta run 1 (evidence/trial/call_b669b105d4774a49/trial.json) reached Delta's conversational AI assistant;
the gate stayed silent and the call ended at 68 s with no human. The owner wants Hermes to route through such
assistants by voice. This opens a new outward speech path, so it needs your YES before the one Delta retry.

TASK: Read-only review on `main` of:
1. L1 commit 74a9d2f: `plugin/line/engine/assistant.py`, its hook in `machine.py`, the finish restrictions,
   `tests/l1/test_r25_assistant.py`, `docs/lanes/L1.md` (round 25). Only fixed phrases may go out while the line is
   `automated_assistant` and before a human: Representative / Agent / Speak to an agent / "<category> question" /
   "No, a general question." (only to an existing-trip question). Never yes, data, callback, payment or change. 8 s
   spacing, 12 per call. After a human: fixed lines only, as before.
2. L2 commits be38d65 and 41877a7 (`plugin/line/detect/`, `tests/l2/`, `docs/lanes/L2.md` rounds 5-6): the
   `automated_assistant` / `hold_music` / `human` split. Look for both errors: a bot labelled human (owner rung for a
   bot, or fixed human lines spoken to a bot) and a human labelled bot (Hermes says "Representative." to the agent and
   never rings the owner).
Replay Delta run 1 through the real engine, then build adversarial cases: a bot that asks for a confirmation number,
offers a callback, asks "is that correct?", or names itself; agents who greet with and without names; transfer lines.
Known: two L1 tests are being updated for round 6 (M3 stale-command, R5 media-streams ingress); ignore those two.

LIMITS:
- Read only in the repo. Write only under `/Users/mac/.hermes/cache/scratch/hermes-line-r1d24/`.
- No network, no calls, no provider access. Read only `~/.hermes-line/approved_destinations.json` under that folder.
- Do not commit or push.

DONE: `REVIEW-DELTA24.md` there with findings and one verdict for the Delta retry: yes or no.
