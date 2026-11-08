# L2 round 5 (coordinator): automated assistant vs human vs hold music

`git merge main`. Evidence: `evidence/trial/call_b669b105d4774a49/trial.json` (Delta run 1). Delta answers with a
conversational AI assistant that speaks naturally ("Thanks for calling." / "Hi." / "I'm not a live person. I'm Delta's
automated assistant." / open questions about known traveler numbers / "I can give you a moment if you need it."). The
detector labelled these `hold_message` and `ivr`, and early lines were "detect_slow".
Add an explicit `automated_assistant` label: a system that converses in natural sentences without "press N" menus,
says it is automated or virtual, asks open routing questions, or repeats patience lines. Keep `human`, `hold_music` /
`hold_message`, `ivr` (keypad menus), `voicemail`, `unknown`. Never call an automated assistant `human`: a bot saying
"Hi" or "How can I help you today?" is not proof of a human; a human needs evidence such as a personal name with a
role, a transfer line ("let me connect you", then a new voice), or L2's existing human cues after a hold. Include
Delta's lines as fixtures (masked), plus realistic variants from other airlines' and utilities' virtual assistants,
and transfer-to-agent sequences. Report detection latency; keep the fast local path under 5 ms where possible.
Same LIMITS, offline only. DONE: tests (0 false humans on assistant fixtures), SHA, the label contract for L1.
