# L1 round 13 (coordinator): owner line mode (scope addition, Vezir decision 2026-11-05)

Queue after round 12, as its own commit. Demo beat 1: the owner calls Hermes from the road, asks it to call a
business, gets bridged in. Keep it small; Nov 10 deliverables come first.
- When a call reaches the line (direct to the Twilio number, or forwarded from `forwarding_number`), the caller first
  gets the normal inbound treatment. Caller ID is NOT authentication: a call from `owner_number` is only offered the
  PIN ("If you are Can, say or key your PIN").
- PIN: set at install, stored through the plugin's secret scope (`requires_env`, e.g. `HERMES_LINE_OWNER_PIN`, 4-8
  digits). Accept DTMF digits, and spoken digits from the ConversationRelay transcript. Compare in constant time.
  3 failed tries -> polite hangup and an event; no hint about which part was wrong. The PIN never reaches Hermes,
  the transcript, logs, events or evidence (mask it like a code). Until it passes, the caller is unknown: take a
  message only.
- After the PIN: the call runs as the owner's own agent. It must still be the restricted call profile (rule 12, N4
  guard unchanged); the owner's chat agent stays the brain for anything else through the existing tools. Allowed:
  questions, "call <business> for me" (creates a normal outbound task; the owner's spoken "yes" after the PIN counts
  as the approval for that one task, bound to the destination Hermes read back), "bring me in when they answer"
  (owner mode on that task, bridging this owner leg or ringing `owner_number`). Read-only requests like "today's
  calendar" go through the chat agent if available; if the call profile cannot do them, say so on the call.
- The call summary lands in the owner's chat as today.
- Tests: wrong PIN x3, PIN by DTMF and by speech, PIN masked everywhere, owner-number caller without PIN gets the
  unknown-caller path, spoken yes binds to the read-back destination only, a second task in the same call needs its
  own yes, a stranger cannot reach owner mode.
Same LIMITS. DONE: test output, SHA, the setting and the `requires_env` entry documented in `plugin/README.md`.
