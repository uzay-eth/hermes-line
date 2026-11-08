# L1 round 30 (coordinator): Delta run 2 false human

`git merge main`. Evidence: `evidence/trial/call_2243b7f205194ab5/trial.json` (Delta run 2, 57 s). Transcript:
Delta "Thanks for calling. I'm Del" (cut off) / Hermes live-person question / Delta "Hi, Hermes." / Hermes asks again
/ Delta "What can I help you with today regarding Delta Travel?" -> L2 slow path `human high late` -> owner rung at
18 s for a bot. Can did not answer in 20 s; the fallback question went to the bot, which answered "You're welcome. What
can no problem."; Hermes said "Thank you, that's very helpful." and the summary reports it as an answer.
Fixes:
1. Do not talk over the business. After the disclosure, the live-person question (and any phrase) waits until the
   business turn has ended and 1.2 s of quiet has passed. The cut "I'm Del…" was the bot's self-declaration.
2. On restricted and information-only tasks, the owner is rung only after the fixed live-person question and a
   shared `clear_yes()` reply, in every call, bot label or not (extend round 29's rule). A late/model human verdict
   alone never rings.
3. Fallback: the closing line is neutral ("Thank you for your time. Goodbye."), never "that's very helpful". The
   summary quotes the reply and says "answer not verified" with outcome `unanswered`, unless the reply has a digit or
   a currency word, then `answered (unverified)`.
Tests: a replay of this transcript through the real engine and detector never rings the owner and asks the question
after quiet; the closing line and outcome on a non-answer. Same LIMITS. DONE: tests, SHA.
