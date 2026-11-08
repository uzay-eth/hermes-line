# L2 round 9 (coordinator): Delta run 2 false human

`git merge main`. Evidence: `evidence/trial/call_2243b7f205194ab5/trial.json`. Lines: "Thanks for calling. I'm Del"
(cut), "Hi, Hermes.", "What can I help you with today regarding Delta Travel?". The fast rules gave unknown
(`detect_slow` three times), then the slow path gave `human high late`. That was Delta's assistant.
Fixes:
- A truncated self-introduction ("I'm Del", "I'm Delta's", "this is Delta's") is bot-leaning, never human.
- "What can I help you with (today) regarding/about <brand> <noun>?", "What do you need help with today for <brand>
  travel?" and similar brand-scoped open questions are `automated_assistant`.
- The slow/model path may never return `human` with high confidence for a line without a personal name or a clear
  affirmative to our question; cap it at `unknown` with `possible_new_party`.
- Switch your detector to the shared `plugin/line/detect/affirmative.py` `clear_yes()` (added by L1 round 29).
Fixtures: this call's lines, plus brand-scoped questions from other airlines and banks. Targets: this replay never
human; 0 false humans on bot fixtures; 0 missed humans on agent fixtures. Same LIMITS, offline. DONE: tests, SHA.
