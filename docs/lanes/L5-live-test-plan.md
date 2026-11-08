# L5 live test plan: first real Grok Voice checks

Status: **plan only**. Nothing here has run. It needs an `XAI_API_KEY`, which does not exist yet.

The design and offline evidence are in [ADR 0004](../adr/0004-realtime-voice.md) and [L5](L5.md). This plan measures
the facts the offline tests could not, and which the docs mark UNCONFIRMED.

## Preconditions (all required; the coordinator checks them, and Can does the owner steps)

1. **TWILIO-GO stands.** The dial allowlist holds **only Can's two own lines**: the Spectrum mobile and the Fi line
   (STATUS.md, TWILIO-GO). Every call below goes to one of them. No third-party, business, toll-free or emergency
   number is dialled, and the plugin's destination gate stays on.
2. **xAI key.** Can provides `XAI_API_KEY` through the owner staging route (like the Twilio key; never in chat or
   git). Can also sets a **prepaid credit or spend limit of at most $3** in the xAI console. xAI is a new paid
   service, so the coordinator posts it as Can's approval before any call.
3. **Code merged.** L1 round 5 (voice wiring, `docs/lanes/L5.md` §2) is merged, all suites pass, and the
   `hermes plugins validate` gate is met.
4. **Tunnel.** The public HTTPS/WSS tunnel is up, and `voice.transport: media_streams` is set for the test profile
   only.
5. **Settings.** Recording stays OFF and the disclosure stays ON. Configuration is `voice.reasoning_effort: none`,
   `idle_hold_s: 60`, with defaults otherwise, except where a check says otherwise.
6. **Before and after every call.** Check the Twilio balance against the coordinator stop line ($4.00, with the
   $3.00 hard floor). Read the xAI console usage before and after each call.

**Evidence per call.** Keep the Twilio Call SID and duration, and the masked transcript. Export the masked
`voice_*` events:

- `voice_open.connect_ms`
- `voice_first_audio.ms`
- `voice_barge_in.played_ms`
- `voice_dtmf.via`
- `voice_dtmf_no_reaction`
- `voice_close`

Also keep `VoiceCall.stats()` (meter seconds, opens, cache hits, refusals) and the xAI usage delta. Store them under
`tests/l5/evidence/live/`, masked, with no keys.

## Rates used (list prices)

| Item | Price | Source |
|---|---|---|
| Grok Voice | $0.08/min | docs.x.ai/developers/pricing, accessed 2026-11-05 |
| Grok text input | $0.004 per text input | same page |
| xAI streaming STT | $0.20/h ($0.0033/min) | same page |
| Twilio US outbound | $0.014/min | twilio.com/en-us/voice/pricing/us, accessed 2026-11-04/05 |
| Twilio Media Streams | $0.0044/min | same page |

Whether Grok bills connected minutes or audio minutes is UNCONFIRMED. The estimates below assume **connected**
minutes, the more expensive case. Check 6 measures it.

## Checks

The checks are grouped into five calls. A call runs only if the previous one passed, or failed for a reason
understood and written down.

### Check 1: Grok connect time on a human answer, and first audio (Call A, to Spectrum, Can answers)

**Steps.**

1. Hermes calls. Can answers and says "Hello, this is Can speaking". L2 asks the live-person question (a cached
   clip after the first render). Can answers "Yes, I'm a real person". The call reaches `agent_talking`.
2. The `voice_open` event gives `connect_ms`: socket plus `session.update` up to `session.updated`.
3. `voice_first_audio.ms` gives the time from `response.create` to the first audio delta. The first mark
   acknowledgement gives the time until audio was actually played toward the phone.
4. **Repeat 5 times in the same call.** Can says "Hold on a second, please hold". Grok should call `wait_on_hold`,
   or the idle rule fires, and the session closes. Can stays silent for 15 s, then says "Hello, I'm back, this is
   Can speaking".

**Passes if:**

- 5 opens and 5 closes, and the meter shows 0 s during each silent stretch;
- `connect_ms` p50 is at most 1500 ms and `first_audio` p50 at most 1000 ms (targets, not documented values);
- Can hears no audio before the disclosure.

**Also checked:** that the `grok-transcribe` input transcription produces `business` transcript lines (the field
name is UNCONFIRMED), and one `ask_hermes` question ("Is Can free on Friday at 7?") with its filler and total
latency.

**Expected:** about 7 min of call, with Grok open about 3.5 min (5 × ~40 s).

### Check 2: barge-in (Call A, continued)

**Steps.** Can interrupts Grok mid-sentence 5 times.

**Measured:** the time from `speech_started` to the `clear` being sent (the plugin's part), Can's report of whether
the voice stopped within about half a second, and `played_ms` in `truncate` against what Can actually heard (the
last word heard).

**Passes if:** 5/5 stops, and Grok's next answer does not repeat words Can did not hear.

**Expected:** about 1 more min of Grok.

### Check 3: key tones heard by a real menu (Calls B and C, to the carrier voicemail of Can's own lines)

The only phone menus reachable without calling a third party are the voicemail systems of Can's own lines. Can
declines the call (or lets it ring out), so it reaches voicemail. The plugin is in `ivr` state.

**Call B (tones).** During the voicemail greeting, Hermes presses the carrier's documented greeting key: `*` or
`#` to skip the greeting or to reach the owner login. Which key a given carrier honours is UNCONFIRMED; try `#`
first, then `*`.

- **Reaction signal:** the greeting stops early, or a beep or PIN prompt follows within 8 s. This shows in the STT
  transcript and in `voice_dtmf_no_reaction` / `voice_dtmf`.
- **Passes if:** the menu reacts to in-band tones.
- If it does not react, the policy should trigger the fallback by itself, and that is recorded.

**Call C (forced fallback).** Same voicemail, this time with the REST redirect (`<Play digits>` and then a new
stream).

- **Measured:** the gap from the old stream's `stop` to the new stream's `start`, whether the digits were honoured,
  whether Twilio requested the old `<Connect action>` URL (NOT FOUND in the docs), and whether L1's drop watchdog
  stayed quiet.
- **Passes if:** the menu reacts, the stream resumes, and the call stays up.

**Never** send a code or any real number. Use only `#` or `*`. Hang up before the beep, or leave no message.

**Expected:** about 2 min per call, Grok about 0 min (cached lines; one cold render of about 10 s if the process is
new).

### Check 4: STT quality over hold music (Call D, to Spectrum, Can plays a hold recording)

**Steps.**

1. Can answers and holds a speaker playing a 10-minute royalty-free hold-music recording to the phone. Can reads
   the hold announcement script below every 2 minutes; the script is known, so word errors can be counted.
2. At an unannounced moment after minute 8, Can says "Hello, thanks for waiting, this is Can speaking."

**Measured:**

- STT utterances per minute over music alone (false text);
- word error on the announcements;
- L2 labels: no false `human`;
- **the meter's Grok seconds during the hold, which must be 0**;
- the delay from Can's first live word to `human_detected` and to the first Grok audio.

**Passes if:** 0 Grok seconds before the live speech, 0 false human, and the live greeting detected.

Note: L2 currently labels "thanks for holding …" as a hold message (L5 §5, item 4). Count it, and do not tune it
during the test.

**Expected:** about 12 min of call, STT 10 min, Grok about 1 min after the human.

**Announcement script:** "Thank you for holding. All of our representatives are currently assisting other
customers. Please stay on the line and your call will be answered in the order it was received."

### Check 5: session length limit, and Check 6: billing basis (Call E, to Fi or Spectrum, Can answers)

**Steps.** After the human is confirmed, set `idle_hold_s` to 600 for this call only, so the plugin does not close
the session. Can then stays quietly on the line for 10 minutes, saying one short sentence every 2 minutes so the
call is not dead.

**Measured:**

- whether xAI ends the session by itself (and when);
- any `error` events;
- the server's `idle_timeout_ms` behaviour (off by default);
- whether reconnect and recovery work if a drop happens.

**Billing basis (Check 6).** Compare the xAI usage delta for this call with the meter. If about 10 min is billed for
a session that was mostly silent, xAI bills connected minutes; if only about 1 min of audio is billed, it bills
audio minutes.

**Result format:** "no limit hit within 10 min", or the limit observed. A longer test is not worth its price: the
plugin closes the session on every hold, and the reconnect path covers drops.

**Expected:** about 11 min of call, Grok about 10.5 min.

## Budget

| Call | Checks | Twilio minutes (est.) | Grok minutes | Grok $ | Text inputs $ (est.) | STT $ | xAI $ | Twilio $ |
|---|---|---|---|---|---|---|---|---|
| A | 1, 2 | 8 | 4.5 | 0.360 | 0.120 (≈30) | 0.003 | **0.483** | 0.147 |
| B | 3 tones | 2 | 0.2 (cold render) | 0.016 | 0.008 | 0.007 | **0.031** | 0.037 |
| C | 3 fallback | 2 | 0 | 0 | 0 | 0.007 | **0.007** | 0.037 |
| D | 4 | 12 | 1 | 0.080 | 0.040 | 0.035 | **0.155** | 0.221 |
| E | 5, 6 | 11 | 10.5 | 0.840 | 0.020 | 0.002 | **0.862** | 0.202 |
| **Total** | | **35** | **16.2** | **1.296** | **0.188** | **0.054** | **≈ $1.54** | **≈ $0.64** |

- **xAI: about $1.54, under the $2 limit.** Hermes inference for `ask_hermes` runs on the existing subscription.
- **Twilio: about $0.64 of the existing balance.** It stays above the $4.00 stop line if the balance is above about
  $4.70 at the start.
- **Stop rules.**
  - Stop the plan when the xAI console shows **$1.80** used. If check E would cross it, shorten E to 5 min (the
    result is then "≥ 5 min").
  - Stop at once on any dial attempt to a number other than the two allowlisted lines.
  - Stop at once on any code or secret in a log.
  - Stop at once on a Grok session open during a hold, or on the Twilio stop line.
- **No reruns without the coordinator.** A rerun of one call costs at most the row above.

## Out of scope here

- Calls to businesses or real IVRs: they need Can's approval of a named number.
- Owner-bridge timing: covered by the S2 plan.
- Sealed codes over a live call: L4's live plan.
- The ConversationRelay path: it needs the addendum and is not part of this plan.
