# L2 round 6 (coordinator, urgent): human agents labelled automated_assistant

`git merge main`. Coordinator check on main (L2 round 5 detector), single-utterance and in sequence after a transfer
line:
- "Thank you for calling Delta, this is Dana. How can I help you today?" -> automated_assistant high
- "Thanks for holding, my name is Marcus with Delta reservations, how can I help?" -> automated_assistant high
- "Hi, this is Dana, how can I help you today?" -> automated_assistant high
- after "Please hold while I transfer you to an agent." the Dana line is still automated_assistant
Cause: `_assistant` matches generic phrases such as "how can I help", "let me connect", "transfer you to" as bot cues,
and they win over the human cues. On the Delta retry this would make Hermes say "Representative." to the real agent
and never ring the owner. This blocks the retry.
Fix:
- "How can I/may I help/assist" and similar offers are neutral, never assistant evidence on their own.
- Transfer lines ("let me connect you", "transferring you", "please hold while I transfer you to an agent") are
  transfer evidence: after one, the next new utterance with a personal name ("this is Dana", "my name is Marcus",
  "Dana speaking") or a named role is `human` (high), unless it declares itself automated/virtual/AI.
- `automated_assistant` needs a self-declaration (automated/virtual/digital/AI assistant, "I'm not a live person"),
  or a bot-only pattern (routing questions about numbers/reservations, patience lines like "I can give you a moment"),
  and is sticky only until a transfer line or a named human greeting.
- A personal-name greeting after hold music / hold messages (no bot declared) is `human` high, as before round 5.
Fixtures: the lines above, real-world agent greetings from airlines, banks, utilities and carriers (with and without
company name, "speaking", "my name is", "this is"), bot greetings that use names ("Hi, I'm Ava, Delta's virtual
assistant") which stay automated. Targets: 0 false humans on bot fixtures AND 0 missed humans on agent greetings
after hold or transfer. Run L1's suite after the change and list any L1 test that changes outcome.
Same LIMITS, offline only. DONE: tests, both counts, SHA.
