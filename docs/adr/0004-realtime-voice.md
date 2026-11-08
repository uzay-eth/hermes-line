# ADR 0004: Realtime voice path: Media Streams + Grok Voice, gated by hold state, Hermes as the brain

Date: 2026-11-05. Status: **built and tested offline with fakes (lane L5); not run against xAI or Twilio**. No xAI
key exists, no call was placed, and no xAI or Twilio API was called. `docs/BRIEF.md` sha256
`346bbac78d9e7054ca55985ed0181b13449d2b7fa359cb593d870ad9d3eb167d` verified. Extends ADR 0003. ConversationRelay
stays the alternative transport, and the transport is a setting.

Every external fact cites the source register at the end. All sources were accessed **2026-11-05** from public
official pages only. **UNCONFIRMED** marks anything those pages do not state.

## Context

The owner wants Grok Voice (xAI) to be the voice on the phone, while the user's own Hermes keeps making decisions and
running tools (CONTRACT.md, owner updates 2026-11-05). Grok Voice costs **$0.08/min** [X3]. A 40-minute hold must
not cost 40 Grok minutes. ConversationRelay also needs the Twilio AI/ML addendum [T8], and the owner cannot reach the
Twilio Console to accept it (STATUS.md, 2026-11-05).

Facts that drive the design:

- **xAI Speech to Speech.** The endpoint is `wss://api.x.ai/v1/realtime?model=grok-voice-latest`, with
  `Authorization: Bearer <key>`, server side only. `grok-voice-latest` is an alias for `grok-voice-3.0` [X1].
- **OpenAI Realtime compatibility.** The API is "compatible with the OpenAI Realtime API", with listed differences
  [X1].
- **Session settings.** In `session.update`, `audio.input.format.type` and `audio.output.format.type` take
  `audio/pcmu` (G.711 µ-law, 8 kHz). `turn_detection.type` is `"server_vad"` or `null`. `idle_timeout_ms` re-engages
  the caller; it does not disconnect [X1].
- **Function tools.** A tool is declared as `{type:"function", name, description, parameters}`. A call arrives as
  `response.function_call_arguments.done`. The result goes back as `conversation.item.create` with
  `function_call_output`, then `response.create`, and "Do not send `response.create` until all function call outputs
  have been submitted" [X1, X2].
- **`force_message`.** This is an xAI extension that "make[s] the agent speak a hard-coded, TTS-synthesized line
  without involving the model". It is its own turn, so no `response.create` follows it. With `interruptible:false`,
  caller audio is dropped until playback ends [X1].
- **Audio delta names.** Audio arrives as `response.output_audio.delta`, or as `response.audio.delta` [X1].
- **Other xAI limits.** Sessions are limited "by concurrent sessions alone", at 10 for tier T0 [X4]. History is
  dropped after 30 min of inactivity [X1]. No maximum session length is published (UNCONFIRMED).
- **xAI Speech to Text.** Streaming runs at `wss://api.x.ai/v1/stt` and takes `encoding=mulaw`, `sample_rate=8000`,
  `endpointing` (0-5000 ms) and `interim_results` [X5]. Audio goes in as raw binary frames. A transcript is final
  when `is_final` and `speech_final` are both true [X5]. The price is **$0.10/h REST and $0.20/h streaming** [X3].
- **Twilio Media Streams.**
  - A call can have one bidirectional `<Connect><Stream>`, and it receives only the inbound track [T1, T3].
  - `<Stream url>` does not support query strings. `<Parameter>` name+value must be under 500 characters [T3].
  - Twilio sends `connected`, `start`, `media`, `dtmf`, `mark` and `stop`. The format is always `audio/x-mulaw`,
    8000 Hz, mono [T2].
  - We send `media` (base64 µ-law with no header), `mark` and `clear`. `clear` "empties all buffered audio and
    causes any mark messages to be sent back" [T2].
  - "Sending DTMF outbound from your media server toward Twilio is not supported" [T1].
  - "If your server closes the connection, Twilio requests the `<Connect>` `action` URL" [T2].
  - Media Streams cost $0.0044/min [T6].
- **The official cookbook example** (`xai-cookbook/examples/voice-agent-phone/xai`) is Node/TypeScript with Express.
  It forwards µ-law both ways unchanged, clears Twilio playback on `speech_started`, and answers tools on
  `response.output_item.done` [C1]. **Its licence is the custom "Beta Testing License"**, Copyright (c) 2025 X.AI
  LLC. It is MIT-like, but "intended solely for testing and evaluation purposes" with no support or updates, and
  GitHub classifies it as "Other" [C2]. **No cookbook code was copied.** This implementation is written from the API
  docs.

## Decision

### 1. Transport and selection

The `voice.transport` setting takes `auto` (default), `media_streams` or `conversation_relay`. `auto` uses Media
Streams + Grok when `XAI_API_KEY` resolves through `agent.secret_scope.get_secret`. Otherwise it uses
ConversationRelay, unchanged. Forcing `media_streams` without a key refuses the call before dialing; it never places
a call with no voice. The key is read only at connect time, sent in one header, and never stored, logged or returned
(`voice/settings.py`, `voice/grok.py`). `XAI_API_KEY` is already a core Hermes env name (xAI provider/TTS).

### 2. The call carries Media Streams; the voice model is gated

The voice model is only connected while the call is in a human conversation. Everything else runs on the stream
alone:

```
Twilio <Connect><Stream> ── µ-law in ──► VoiceCall ──► xAI STT (only while no conversation is open) ──► L1 prompt frames ──► L2 HoldTracker
                        ◄── µ-law out ─           ──► Grok session (only while the gate says open)
                                                  ──► phrase cache (fixed lines, no session)
```

`SessionGate` (`voice/gate.py`) decides from L1's call state.

| L1 state | Voice session |
|---|---|
| `dialing`, `ivr`, `on_hold` | **closed** (open only to render a fixed line that is not cached) |
| `human_detected`, `agent_talking` | open |
| `ringing_owner` | open if it followed a human conversation (keeps the person company); closed for a pane take-over from hold |
| `owner_connected`, `awaiting_approval`, `ended`, `failed` | closed, always |
| back to `ivr`/`on_hold` | closes ("hold resumed") |

Hold resumption is detected three ways: L1 moves the state, Grok calls `wait_on_hold`, or nobody speaks for
`idle_hold_s` (60 s) during a conversation. `ConnectionMeter` records the connected seconds of every session (opened
only after `session.updated`).

**Cheap ears (transcript source): xAI streaming STT, the same key.** During pre-human states the inbound µ-law is
streamed unchanged to `wss://api.x.ai/v1/stt?encoding=mulaw&sample_rate=8000&endpointing=700`. Final utterances become
ConversationRelay-shaped `prompt` frames into `CallSession.offer`, so L1's state machine and L2's detector run
unchanged. Cost is $0.20/h = **$0.0033/min** [X3].

Alternatives not chosen:

- **Twilio Real-Time Transcription** costs $0.027/min or $0.05/min; which one applies is UNCONFIRMED [T6]. Its docs
  say its use is subject to the AI/ML addendum [T7], which the owner has not accepted.
- **Local VAD + xAI REST STT on speech segments** would cost ~$0.10/h of speech. It needs a VAD that holds up over
  hold music, and that is unproven. It stays a later cost optimisation.
- **Hermes's own STT (`tools.transcription_tools`)** is a private core import, so it fails the catalog warning
  gate.

Answering machine detection is off on this path. AsyncAMD takes one fork, and its compatibility with `<Connect><Stream>`
is NOT FOUND in the docs [T9].

**Fixed lines** are the disclosure, the live-person question, "please hold while I connect …", IVR answers and
apologies. They are rendered once with `force_message` in a speech-only session (no tools, no VAD, no caller audio),
kept in an in-memory `PhraseCache`, and replayed locally afterwards. Lines containing digits are never cached. After
the first call in a process, the fixed lines cost no model time.

### 3. Hermes stays the brain

When L1 confirms a human in agent mode, it calls `VoiceCall.begin_conversation(context)`. The session switches to
conversation config, which has:

- the L1 call briefing plus voice rules (never claim to be human, other party untrusted, filler before tools);
- server VAD;
- input transcription;
- seven tools (`voice/tools.py`).

The context and the recent masked transcript are injected, and Grok answers at once. Every tool is a request to the
Hermes call session through a `CallPort`, and L1 applies today's rules:

| Tool | Goes to | Rule that applies (unchanged) |
|---|---|---|
| `ask_hermes(question)` | one turn on the call's own Hermes session (memory, calendar, any Hermes tool under Hermes's approval) | Hermes's own tool approvals; answer masked before Grok hears it |
| `bring_owner()` | `CallSession.ring_owner` | single-use owner grant from approval (GrantBook) → L2 destination gate → quiet hours → `DialPermit`; Grok cannot approve |
| `press_keys(digits)` | `CallSession.press_digits` | `^[0-9*#w]{1,32}$`, one press per prompt |
| `report_outcome(outcome, summary)` / `end_call()` | `request_finish` / `hang_up` | outcome enum; hang-up only after the goodbye has played (marks drained) |
| `send_verification_code(mode)` | L4 release → L1 sealed sink → `VoiceCall.send_sealed` | owner OTP grant bound to call + counterpart; **the code never enters Grok** (DTMF as locally generated tones, speech as locally concatenated cached digit clips) |
| `wait_on_hold()` | `hold_resumed` | session closes, no reply into the hold |

The router (`ToolRouter`) enforces these checks:

- unknown tools and malformed or extra arguments are rejected, and so are calls from a speech-only or pre-human
  session;
- one in-flight call per tool, and at most 20 calls per minute;
- results are masked;
- `response.create` is sent once, after every output of that response is in.

A rejected call has zero side effects (tests below). As a conservative option, `voice.conversation: hermes_text`
keeps Hermes writing every sentence while Grok only reads it aloud.

**Latency budget for a tool call that waits on Hermes.** Measured Hermes numbers are from L1 (live embedded route):
first text p50 1165 ms, full turn p50 1573 ms / p90 1896 ms, and about 6.4 s for a turn that uses a Hermes tool
behind tool search (docs/lanes/L1.md §7.2).

| t | What the caller hears |
|---|---|
| 0 | The person finishes the question. Server VAD end-of-turn takes UNCONFIRMED time. Grok says its own filler ("Let me check that for you"), as instructed, then calls `ask_hermes`. |
| ~1.6-1.9 s | The Hermes answer arrives for a plain question (p50/p90). The function output is sent, then `response.create`, and Grok speaks the answer. Grok's time to first audio is UNCONFIRMED. |
| 3.0 s | If Hermes is still busy, the plugin plays "One moment please, I'm still checking." (cached, or `force_message`). |
| 10 s | Hard cap (`ask_timeout_s`). The tool returns "did not answer in time", and Grok says it will confirm later. Nothing is invented. |

The target is under 2.5 s from end of question to the start of the answer for a plain question. It is not measured,
because no xAI key exists.

### 4. Barge-in, marks, DTMF

**Barge-in and marks.** After every audio delta the bridge sends a `mark`, so the controller knows how much of each
answer Twilio actually played. On `input_audio_buffer.speech_started` while audio is playing, the controller does
three things:

- sends Twilio `clear`;
- drops queued audio that was never sent;
- sends `conversation.item.truncate` with the heard milliseconds (8 µ-law bytes per ms).

Server VAD cancels the response itself [X2]. The truncate field names follow OpenAI; xAI documents the event by name
only (UNCONFIRMED).

**DTMF.** Outbound DTMF over the socket is unsupported [T1], so keys are played as **in-band Q.23 tones**
(120 ms tone / 80 ms gap) generated locally. The tests verify the tone pairs with Goertzel. Whether the far-end IVR
detects them through Twilio's media path is **UNCONFIRMED** until a real call.

The documented fallback exists only as separate pieces [T4, T5]: a REST call update to `<Play digits>` followed by a
new `<Connect><Stream>`. It ends the stream, and whether `<Connect action>` fires on an update is NOT FOUND. It would
also put digits into TwiML, so it must never carry a verification code. It is designed, but not built.

### 5. Recovery

**Provider drop.**

1. Retry up to `max_reconnects` (default 2) with backoff (0.5 s, 1 s).
2. Lines that were not yet rendered are re-rendered.
3. A conversation is re-seeded with its context and recent masked transcript, plus "apologize briefly and continue".
4. If every retry fails, `CallPort.voice_failed` is called once, and L1 hangs up with `provider_error`.

**Stream drop.** The session closes. L1's existing `<Connect action>` reconnect path returns new stream TwiML, and a
new stream re-opens the session if the gate still says so.

**Hand-off.** At owner take-over, L1's existing `end` + `handoffData` closes the stream from our side. Twilio then
requests the `<Connect action>` URL, and L1 already returns the conference TwiML there [T2]. The rehearsal test
exercises this.

### 6. Addendum

The Media Streams overview, WebSocket messages and `<Stream>` TwiML pages [T1-T3] **do not mention** the Predictive
and Generative AI/ML Features Addendum. ConversationRelay onboarding requires accepting it [T8]. The `<Transcription>`
page says that use of it is subject to the addendum [T7]. So, by the public docs, the Media Streams + Grok path
**needs no addendum**: it uses neither ConversationRelay nor Twilio transcription, and AMD is off. Whether the account
enforces anything at runtime is UNCONFIRMED until the first real call after TWILIO-GO.

## Cost per call type (list prices; calculation)

**Rates used:**

- **Twilio US** [T6; S2 for outbound/conference]: outbound $0.014/min, Media Streams $0.0044/min, ConversationRelay
  $0.07/min, conference $0.0018 per participant-minute.
- **xAI** [X3]: Grok Voice $0.08/min, plus $0.004 per text input; STT streaming $0.20/h.

**Assumptions:**

- The STT stream runs for the whole pre-human time.
- About 10 text inputs per conversation (context, tool outputs, fixed lines).
- The phrase cache is warm.
- A cold cache adds about 10 s of Grok (≈ $0.013) the first time per process.

**UNCONFIRMED:** whether Grok bills connected minutes or audio minutes (the meter assumes connected, the conservative
case), what counts as a "text input", per-minute rounding on either bill, and taxes. Hermes inference (`ask_hermes`,
the summary) runs on the user's existing Hermes subscription and is not priced here.

| Call | Grok path (this ADR) | ConversationRelay (ADR 0003) | Grok kept open the whole call (naive) |
|---|---|---|---|
| **40-min hold, then 5-min human, Hermes finishes (45 min)** | PSTN 0.630 + Streams 0.198 + STT 0.133 + Grok 5 min 0.400 + text ≈0.040 = **≈ $1.40** | 45 × 0.084 = **$3.78** | 0.630 + 0.198 + 3.600 = **$4.43** |
| same, but owner bridged at the human (≈30 s of Grok, 5 min conference) | 0.630 + 0.178 + 0.133 + 0.040 + 0.012 + owner leg 0.074 + conference 0.018 = **≈ $1.09** | 40.5 × 0.084 + 0.074 + 0.018 + 4.5 × 0.014 ≈ **$3.56** | ≈ $4.1 |
| **2-min restaurant call, person answers at once** | PSTN 0.028 + Streams 0.009 + STT ≈0.001 + Grok ≈1.9 min 0.152 + text ≈0.024 = **≈ $0.21** | 2 × 0.084 = **$0.17** | ≈ $0.21 |

Grok's hold-gated path is about 2.7× cheaper than ConversationRelay on a long hold. On a short call with no hold it
is about 25% dearer on paper, and it moves the per-turn reasoning off the user's Hermes model, which ConversationRelay
runs for every sentence.

## Consequences and gates

- **Code.** `plugin/line/voice/`, with no change to L1, L2 or L4 code. L1 must make the integration calls listed in
  `docs/lanes/L5.md`.
- **Dependencies.** `websockets` (a Hermes core dependency at Python ≥ 3.14) must be declared in `plugin.yaml` by
  L1. `XAI_API_KEY` must be added to `requires_env`.
- **Data handling.** Call audio goes to xAI: hold and IVR audio to STT, the human conversation to Grok. The README
  must disclose this (catalog rule 13). xAI retention is UNCONFIRMED. Recording stays off.
- **Before enabling for users.** Run one real call after TWILIO-GO and with an xAI key the owner provides (a paid
  service, which needs the owner's approval and a spend cap). It must measure:
  - in-band DTMF on a real IVR;
  - session open latency at the human;
  - barge-in;
  - `grok-transcribe` input transcription;
  - xAI STT quality over hold music;
  - the billing basis;
  - whether the account demands the addendum.
- **Evidence now.** `tests/l5/evidence/unittest.txt`: 54 offline tests, including a 40-minute virtual hold
  (120,000 real-size frames) with 0.0 connected seconds, on fakes and on the real L1 engine with L2.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| Grok session open for the whole call | 40 voice-model minutes for a 40-minute hold ($3.20 of $4.43 above). |
| Grok as an independent brain | The brief and ADR 0002 require the user's Hermes; Grok gets tools that only reach Hermes and L1's gates. |
| Twilio `<Say>`/Polly for fixed lines | It needs TwiML redirects that end the stream; the voice would differ from Grok. |
| Copying the cookbook | Custom beta-testing licence [C2]; the stack (Node/Express) does not fit a Python Hermes plugin. |
| REST `<Play digits>` redirect as the default DTMF path | Ends the stream on every key press, the `action` behaviour is unconfirmed, and digits would land in TwiML. Kept as a fallback design for menu digits only. |
| Twilio Real-Time Transcription for hold | Addendum by use [T7], and 8-15× the price of xAI STT. |

## Source register (all accessed 2026-11-05)

- X1: [xAI Speech to Speech](https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech). Endpoint,
  models, OpenAI compatibility, session parameters, `force_message`, tools, event naming, resumption. The
  `force_message` and session-parameter sections were read from the page HTML directly.
- X2: [xAI Voice API reference](https://docs.x.ai/developers/rest-api-reference/inference/voice). Client and server
  event lists, `response.cancel` ("In VAD mode, interruptions are automatic"), `function_call_output`, STT REST path.
- X3: [xAI pricing](https://docs.x.ai/developers/pricing). "Speech to Speech (grok-voice-3.0) | $0.08 / min ($4.80 /
  hr)", "$0.004 / text input", "Speech to Text | $0.10 / hr (REST), $0.20 / hr (Streaming)".
- X4: [xAI rate limits](https://docs.x.ai/developers/rate-limits). Speech to Speech is limited by concurrent sessions
  (T0: 10).
- X5: [xAI Speech to Text](https://docs.x.ai/developers/model-capabilities/audio/speech-to-text). `wss://api.x.ai/v1/stt`,
  query parameters, binary frames, `transcript.partial`, `is_final`/`speech_final`.
- X6: [xAI Text to Speech voices](https://docs.x.ai/developers/model-capabilities/audio/text-to-speech). Default voice
  `eve`.
- C1: [xai-cookbook voice-agent-phone](https://github.com/xai-org/xai-cookbook/tree/main/examples/voice-agent-phone/xai).
- C2: [xai-cookbook LICENSE](https://github.com/xai-org/xai-cookbook/blob/main/LICENSE). "Beta Testing License".
- T1: [Twilio Media Streams overview](https://www.twilio.com/docs/voice/media-streams). One bidirectional stream; no
  outbound DTMF; 4-fork limit.
- T2: [Media Streams WebSocket messages](https://www.twilio.com/docs/voice/media-streams/websocket-messages).
- T3: [TwiML `<Stream>`](https://www.twilio.com/docs/voice/twiml/stream). Inbound track only for `<Connect>`; no query
  string; `<Parameter>`.
- T4: [TwiML `<Play>` digits](https://www.twilio.com/docs/voice/twiml/play).
- T5: [Call resource](https://www.twilio.com/docs/voice/api/call-resource). Update an in-progress call with new
  TwiML.
- T6: [Twilio US voice pricing](https://www.twilio.com/en-us/voice/pricing/us). Media Streams $0.0044, Conversation
  Relay $0.07, Streaming Transcription $0.027, Transcription $0.05, AMD $0.0075.
- T7: [TwiML `<Transcription>`](https://www.twilio.com/docs/voice/twiml/transcription). AI/ML addendum by use;
  `statusCallbackUrl` events.
- T8: [ConversationRelay onboarding](https://www.twilio.com/docs/voice/conversationrelay/onboarding). Addendum
  acceptance required.
- T9: [Answering Machine Detection](https://www.twilio.com/docs/voice/answering-machine-detection). AsyncAMD consumes
  one fork.
- S2 rates (outbound, conference) are from [docs/spikes/S2-telephony.md](../spikes/S2-telephony.md) T5/T13, accessed
  2026-11-04.
