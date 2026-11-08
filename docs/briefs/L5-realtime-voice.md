# Brief L5: Realtime voice path (Grok Voice default, pluggable)

## PURPOSE
Hermes Line is a Hermes Agent plugin for the Nous plugin hackathon (deadline 2026-11-11 14:00 PT): Hermes phones businesses for the user, waits on hold, detects a human, then bridges the owner in or finishes the task. Can wants xAI Grok Voice as the speaking voice, while the user's own Hermes stays the brain for decisions and tools. A 40-minute hold must not cost 40 voice-model minutes. No xAI key exists yet, so this lane builds the path and proves it with fakes.

## TASK
Read `docs/BRIEF.md` (verify sha256 346bbac78d9e7054ca55985ed0181b13449d2b7fa359cb593d870ad9d3eb167d, else stop), `AGENTS.md`, `docs/CONTRACT.md`, ADR 0002, ADR 0003, `docs/spikes/S2-telephony.md`, `docs/lanes/L1.md`, `docs/lanes/L2.md`.
1. Research from official docs only (URL + access date per fact): xAI realtime API (`wss://api.x.ai/v1/realtime`, model `grok-voice-latest`, OpenAI-Realtime-compatible events, `audio/pcmu` 8 kHz, server VAD, function tools, session lifecycle, price per minute on docs.x.ai), the official Twilio example at github.com/xai-org/xai-cookbook/tree/main/examples/voice-agent-phone, Twilio Media Streams (bidirectional `<Connect><Stream>`, mark/clear, DTMF events, sending digits mid-stream), and whether Media Streams needs the Twilio AI/ML addendum that ConversationRelay needs.
2. Design (ADR 0004): Media Streams carries the call. During IVR and hold, no voice-model session is open; audio goes to the cheap path (AMD and the existing L2 detector, fed by a transcript source you choose and document: e.g. Twilio real-time transcription on the call, or local VAD plus on-demand short STT; state its cost). The voice session opens only when L2 says human, or when an IVR prompt needs speech, and closes when the human part ends or hold resumes. Hermes stays the brain: Grok gets function tools that call into the Hermes call session (check calendar or other Hermes tools via an `ask_hermes` style tool, bridge owner, end call, report outcome, send sealed OTP), and every tool goes through the same approval, destination and OTP rules as today. State the latency budget for a Grok tool call that waits on a Hermes turn and what Grok says while it waits.
3. Build in `plugin/line/voice/`: a `RealtimeVoiceProvider` interface (connect, send audio, receive audio, tool calls, interrupt/clear, close), the Grok adapter, a Twilio Media Streams bridge (µ-law passthrough, no transcoding, barge-in via `clear`, marks), the hold gate that opens and closes the provider session, and a fake realtime provider plus a fake Media Streams client for tests. Keep ConversationRelay working as the alternative transport; the provider choice is a setting. The voice provider key comes only through `requires_env` / `agent.secret_scope.get_secret` (name it `XAI_API_KEY`), optional: without it the plugin uses ConversationRelay.
4. Tests: session never open during simulated hold (assert provider-connected seconds = 0 across a 40-minute virtual hold fixture), opens on human, closes on hold resume, barge-in clears playback, tool calls enforce approval/destination/OTP rules (zero side effects when rejected), provider disconnect mid-call recovers or ends cleanly.

## LIMITS
- No network beyond reading public docs and the cookbook. No xAI or Twilio API calls, no calls, no purchases, no keys.
- Write only `plugin/line/voice/`, `tests/l5/`, `docs/lanes/L5.md`, `docs/adr/0004-*.md`. Commit locally on your branch; no push.
- Do not edit L1's engine or server; define the hooks you need from L1 in `docs/lanes/L5.md` ("L1 integration points") with exact function signatures.
- Do not copy code from the cookbook without keeping its licence terms; say which licence it has.
- Tests run offline. Mark every fact you could not confirm as UNCONFIRMED.
- Owner decisions: if you need a decision from Can (the owner), do not ask Can and do not write a decision block yourself. Stop at that point and put the question in your final report to the coordinator: the options (A, B, ...), your recommendation and a one-line reason. Only the coordinator posts owner-decision blocks.

## DONE
- ADR 0004 with the design, sources and cost per call type (40-min hold then 5-min human; 2-min restaurant call).
- `tests/l5/` output.
- `docs/lanes/L5.md` with L1 integration points and the addendum answer.
- Final report: commit SHA, test counts, cost table, addendum answer, open risks.
