# L1 round 25 (coordinator): speak to automated assistants (Delta run 1)

`git merge main`. Evidence: `evidence/trial/call_b669b105d4774a49/trial.json` (Delta run 1, 68 s). Delta answers with a
conversational AI assistant ("I'm not a live person. I'm Delta's automated assistant." then open questions such as
"For the known traveler number, are you trying to add or update it? Or are you trying to verify whether it's already on
a reservation?" and "I can give you a moment if you need it."). The gate correctly sent nothing, and Hermes finished the
task as failed after 70 s. Owner decision (Can via Vezir): this is the core use case and must work.
Build, for restricted and information-only tasks, a speech path for automated systems only. It applies only while the
current detection is `automated_assistant` (L2 round 5 adds this label; until it lands, use L2's `ivr` label when the
text has no "press N" / "say X" announcements) and never after a human is detected (fixed-line rule unchanged):
- Hermes may send only fixed phrases chosen by the engine, never model text: "Representative." / "Agent." /
  "Speak to an agent." / "<category> question" where category comes from a task option `ivr_category` (approved
  values: baggage, reservations, general information) / "No, a general question." only as the answer to a question
  that asks whether this is about an existing trip, reservation or booking. If the assistant loops or repeats, say
  "Agent." again, at most once every 8 s and at most 12 times per call.
- The model may choose among these phrases (a tool `ivr_say(phrase_id)`), or the engine picks by rule; never free text.
- Never answer yes, never give a name, number, confirmation code, date, payment or identity data, never ask for a
  callback, change or payment, whatever the bot asks. If the bot asks for such data, say "Agent." (or stay silent if
  the loop limit is reached).
- The DTMF/menu gate (round 24) stays as is for keypad menus.
- On restricted hold tasks Hermes must not finish the task as failed while the call is up and the global time cap is
  not reached; it keeps routing, then holds. Only business hangup, the cap, or the owner ends it.
- No disclosure-and-live-person question to a system that has already said it is automated; the disclosure line at
  call start stays.
- Log every action with the detection state that allowed it (`ivr_action {state, phrase_id}`).
Tests: a replay of this call's transcript through the real engine reaches "representative" within two assistant
turns and never sends anything else; data requests get "Agent."; loop limit; human detected -> fixed lines only;
finish-failed refused while the call is up. Same LIMITS. DONE: tests, SHA.
