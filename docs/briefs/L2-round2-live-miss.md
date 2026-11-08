# L2 round 2 (coordinator): live call miss

`git merge main`. On the first live call (`evidence/trial/call_68921278eab742c6/trial.json`) the detector gave
`unknown/low` twice to a real person: after the disclosure, "Hi. How are you?", and after the live-person question,
"Yeah. It's me, Ken. But information you have about me?". The second one is an explicit yes to "am I speaking with a
live person". Add both as held-out cases (masked), find why they missed, and fix without raising false-human on the
existing 31 held-out non-human cases (report before/after on the full held-out set). Answers to the live-person
question ("yes", "yeah", "speaking", "it's me", a name, a question back) should confirm a human at high confidence
when they follow that question within 10 s. Same LIMITS as your brief. DONE: before/after table, test output, SHA.
