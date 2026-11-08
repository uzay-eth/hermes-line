# S2: Telephony design spike

Date: 2026-11-04 (America/Los_Angeles). Research only; no calls, APIs, accounts, purchases or credentials accessed. `docs/BRIEF.md` SHA-256 verified: `346bbac78d9e7054ca55985ed0181b13449d2b7fa359cb593d870ad9d3eb167d`. Read AGENTS.md and ADR 0001. Graft map/ask returned an empty index; no implementation exists to inspect in this checkout.

All external factual claims cite the official source register below, accessed **2026-11-04**. Proposed behavior, acceptance targets and calculated costs are engineering recommendations, not measured facts. **UNCONFIRMED** marks anything not established from a public official page. No design skills apply: this is a telephony research task without frontend work. No skills, packages or tool updates were installed.

## 1. Voice loop

**Choose ConversationRelay → plugin text adapter → the user's Hermes agent → streamed text back to ConversationRelay.** Twilio supplies speech recognition/synthesis; Hermes retains memory, tools and task authority [T1]. This is a reliability judgment based on fewer custom audio components, not a measured comparison.

| Dimension | ConversationRelay | Bidirectional Media Streams + realtime speech model |
|---|---|---|
| Plugin input/output | Receives setup, recognized text prompts and interruption/DTMF events; sends text tokens, media requests and digits [T2]. No raw audio channel in this protocol. | Receives raw audio; sends audio. Payload is base64 μ-law, 8 kHz [T3,T4]. Text transcripts require an additional recognizer/model. |
| Latency | Streaming avoids waiting for a complete generated answer [T2]. Actual end-of-utterance → audible reply with Hermes is **UNCONFIRMED**. | Audio pipeline, turn detection and model scheduling are application work [T3,T4]. Actual comparative latency is **UNCONFIRMED**; there is no same-workload benchmark here. |
| Barge-in | Configure speech interruption and input reporting during agent speech [T1]. Handle interrupt events and truncate Hermes's spoken history [T2]. | Implement VAD/turn cancellation and queue accounting; `clear` flushes Twilio audio and `mark` tracks playback [T4]. |
| DTMF mid-call | Native `sendDigits` message; accepts `0-9`, `w`, `#`, `*` [T2]. | DTMF events are inbound only; outbound DTMF over the bidirectional socket is unsupported [T3]. Redirecting to `<Play digits>` would interrupt the stream; smooth return is **UNCONFIRMED**. |
| Silent hold | Send no speech tokens; keep session listening. This is our policy, not a free/pause billing mode. | Send no output audio; continue receiving input. Model silence billing depends on provider: **UNCONFIRMED** without a selected model. |
| US outbound minute | $0.014 PSTN + $0.070 Relay = **$0.084**, plus existing Hermes inference [T5]. | $0.014 PSTN + $0.0044 stream = **$0.0184**, plus speech model/STT/TTS and Hermes [T5]. |
| Brain ownership | Direct text interface fits Hermes. | A speech-to-speech model could become a competing brain. Must constrain it to transcription and rendering of Hermes-approved text. Extra model, credentials, cost and orchestration are outside the approved stack. |

No external realtime model is selected or authorized. Its exact per-minute price is **UNCONFIRMED**: total is `0.0184 + input_tokens/min × input_rate + output_tokens/min × output_rate + any separate STT/TTS`, with rates expressed per token. A lower stream transport charge does not establish a lower full-loop price. No paid comparison run is planned.

Proposed initial configuration: English, documented supported STT/TTS pair, no external speech-provider key; speech barge-in, DTMF detection and input reporting enabled. First utterance discloses the AI role. In hold state there are no filler replies. Send only finalized utterances to the classifier; stream Hermes response tokens with turn IDs. An interruption invalidates pending output for that turn and updates the actual spoken prefix, so tools cannot assume the caller heard an unsaid sentence. A timeout or model failure must never produce invented task success.

Enable `applicationReconnect=true`. Twilio documents bounded replay/reconnect behavior [T6]; persist call/session state locally and suppress repeated tool/DTMF/bridge actions on replay. Persist intent before sending a side effect; on ambiguous delivery, inspect state rather than replaying digits blindly. Reconnect does not promise lossless recovery from arbitrary outages. Probe a short disruption in R6 and log any missing utterance.

**Execution prerequisite:** ConversationRelay requires accepting the Predictive and Generative AI/ML Features Addendum [T7]. Whether Can's account already accepted it is **UNCONFIRMED**. Stop onboarding at that boundary; the coordinator must route any required owner decision. This document selects a technical stack but authorizes no acceptance or account action.

## 2. Hold and human detection

AMD measures speech/silence in the initial greeting, can misclassify, and times out (default 30 seconds; configurable 3–59). `AsyncAmd=true` allows call handling to continue while a callback returns human/machine/fax/unknown. `Enable` returns early; `DetectMessageEnd` waits for a machine greeting ending [T8]. It is not a continuous queue monitor. Never interpret its initial `human` result as proof of a human appearing 30 minutes later. Use it as an optional diagnostic on R2/R8 only; $0.0075 per invocation [T5].

Recommended state machine: `DIALING → LISTENING ↔ IVR ↔ HOLD → HUMAN_CANDIDATE → HUMAN_CONFIRMED → BRIDGING or AGENT_ACTIVE → ENDED`, with separate `VOICEMAIL`, `UNKNOWN` and `FAILED` outcomes. No duration-only transition can declare a human.

| Class | Proposed evidence | Conservative action |
|---|---|---|
| Hold music | No lexical content, repeated musical interval, sustained non-speech | Stay silent; silence alone is UNKNOWN, not proof of music. |
| Recorded hold message | Repeated near-identical finalized phrase, queue/wait language, lack of contingent response | Cache phrase and suppress repeated Hermes calls; retain timestamps. |
| IVR prompt | Explicit numbered options, request to speak or press a key, timeout instruction | Hermes selects only within approved task scope; execute validated digits once per prompt/turn. |
| Voicemail | Message/beep instructions, personal greeting plus no contingent reply; optional initial AMD evidence | Do not bridge or leave a message without task authorization; classify uncertain short greetings as UNKNOWN. |
| Live human | New task-relevant greeting followed by contingent conversational response | Candidate first; brief disclosure/confirmation when appropriate, then confirm and bridge/continue. A recorded “hello” alone is insufficient. |

Transcript-only rules are cheap but cannot hear a beep or distinguish instrumental music from a dead audio path. Audio cues (VAD, spectral repetition, beep detector) can support a classifier but do not prove personhood; recorded speech can sound human. A Media Streams implementation would expose those samples [T3,T4]. **Chosen hybrid is transcript rules + Hermes semantic classification + call/transport health + optional initial AMD**, not an assertion that Relay exposes music/audio features. Concurrent raw monitoring alongside Relay, feature compatibility and its incremental bill are **UNCONFIRMED** and excluded from the initial implementation/budget.

ADR 0001's recorded Jev decision points prohibit a shipped TypeSafe dependency/new paid key. Use deterministic repeat suppression first and the existing Hermes model for ambiguous human/IVR/voicemail decisions. Jev is suitable for this classification in principle but unavailable within this spike's authorization; no new key, provider or override is introduced. Return an enum, evidence spans and uncertainty, not an uncalibrated numeric “probability.” Proposed confirmation threshold: two contingent speech turns, or an explicit contextual answer to one confirmation question. Low confidence means continue listening; never ring the owner solely on a score. These thresholds remain **UNCONFIRMED** until measured.

For 30+ minutes, keep the speech connection active throughout; do not cycle it off on a timer and risk missing a quiet human. Bound context to recent turns plus task state; cache recurring prompts and call Hermes only on novel/changed phrases. Monitor socket heartbeat independently of audio silence. Use a local watchdog and finite call deadline, with graceful task failure at budget exhaustion. Provider maximum usable silent/session duration in this account is **UNCONFIRMED**; R3 supplies evidence for 35 minutes only. At $0.084/min, 35 minutes costs $2.94 before inference/tax [T5; calculation].

Accuracy measurement uses timestamped labels and repeated-message/human confusers, not third-party calls. The offline corpus and release gates are specified in the test plan. Report a confusion matrix, false owner rings per hold-hour, missed human events, detection delay and UNKNOWN rate. Separate transcript classifier accuracy from end-to-end speech recognition performance; neither AMD marketing accuracy nor fixture success demonstrates production reliability.

## 3. Bridge, takeover and cleanup

Choose **Conference at handoff**, not an always-on conference. Conference provides participant lifecycle controls and callbacks [T9,T10]; direct `<Dial><Number>` provides a simpler two-party connection with called-party pre-connect TwiML [T11,T12]. Both still incur PSTN legs. US conference adds $0.0018 per participant-minute [T5,T13]. A two-outbound-leg conference therefore costs `2×0.014 + 2×0.0018 = $0.0316/min`, with Relay removed. Conference is favored for explicit cleanup/control, not an assumed latency advantage.

Proposed sequence:

1. Keep business on Relay while an approved owner leg rings the separate Spectrum phone (number supplied privately via coordinator). Use the owned Twilio number as owner-leg caller ID. Never dial the forwarded Fi identity as the owner target.
2. Owner answers into a private TwiML whisper: task summary and “press 1 to take over,” with a short timeout. No conference join until acceptance; owner voicemail must not hear the business. This is a standalone outbound call's answer TwiML. Direct Dial's alternative is `<Number url>` [T11].
3. On acceptance, reserve a unique conference ID in durable state; stop Hermes speech and end Relay through its documented `end`/Connect-action handoff [T2,T7]. Route the existing business Call SID into `<Dial><Conference>`; do not redial the business. Owner then joins the same room. Set `beep=false`, `waitUrl=""`, recording off and bounded waiting. Check business liveness again before joining owner. Transition audio gap is **UNCONFIRMED** until R4.
4. Business participant uses `endConferenceOnExit=true`; owner uses false, allowing a deliberate return-to-agent flow if requested [T9]. Default takeover is exclusive: Hermes stops speaking and post-takeover transcript is unavailable unless a separately validated observer is added. Do not fake a complete transcript.
5. “Hang up” ends every tracked active/ringing leg and the conference; use call status updates/cancellation as appropriate [T14]. A business hangup cancels a ringing/whispering owner. Owner busy/no-answer/decline leaves Relay on the business, with no automatic repeat dial; continue only within the approved task or finish with an honest failure report.

Return to Hermes after owner departure is a new transition: redirect the still-live business call to Relay, restore task context, confirm socket setup. It is not automatic recovery merely because a conference participant leaves. If cleanup/redirect fails, bounded watchdog termination is safer than orphan billing. Webhook duplicates/out-of-order events must be idempotent and terminal state monotonic. Callbacks carry state updates, not permission to place additional calls.

## 4. Fi identity, forwarding and callbacks

Twilio verified outgoing caller ID requires a verification call: the person answering enters Twilio's validation code, then the verified number can be used as outgoing identity [T15]. Can must answer the Fi phone with forwarding disabled during verification. Test Fi caller identity by calling the separate Spectrum line; own-line fixture calls to Fi use the new Twilio identity to avoid unconfirmed self-call routing. Do not collect/log the validation code; owner-only Console flow is preferable. Verification does not port the Fi number or configure inbound routing; whether caller display/spam handling works on Can's receiving carrier is **UNCONFIRMED** until R1.

**Fi supports forwarding received calls to a separate number.** Official choices are Don't forward, Ring all numbers (simultaneous primary/forwarded ringing), and Always forward (one forwarding target; primary does not ring). Set it in Fi website/app → Home → Phone settings → Call forwarding → choose mode → Add a number → verify → Save. Verification offers a call alternative to SMS [G1]. External Twilio-local-number acceptance on this account is **UNCONFIRMED** until V2/R7. Conditional **no-answer and busy forwarding are UNCONFIRMED**: the official help page does not document them. Do not claim unconditional forwarding is unsupported or substitute Android dialer settings/MMI codes.

Recommendation: Always forward the spare Fi identity to the newly purchased Twilio local number, exactly as the brief intends; bridge to the separate Spectrum line. This prevents a forwarding loop. Add a hop/active-call guard and never return a callback by dialing its untrusted From field automatically. An inbound callback initially has a candidate task match, not authenticated task ownership; ask when ambiguous. Forwarded caller identity preservation, loop behavior and carrier voicemail timing are **UNCONFIRMED**, tested in R7.

If forwarding/verification fails, keep Fi for SMS and outbound identity, publish/use the new Twilio number as the explicit callback number in the spoken request. Callback to Fi then cannot be claimed as automated. Do not port Fi or enable phone-side forwarding workarounds. Any change to the intended identity/callback experience goes to the coordinator before execution.

## 5. Public HTTPS/WSS endpoint

| Route | Official capabilities/limits | Recommendation |
|---|---|---|
| Cloudflare quick tunnel | No account/domain; temporary hostname changes on restart, no uptime guarantee, 200 in-flight requests, no SSE [N1]. Cloudflare proxies WebSockets [N2]. | Only an attended smoke-test fallback; update both URLs before dialing, never restart during a call. |
| Cloudflare named tunnel | Publishing requires account, Cloudflare domain and connector host [N3]; WebSocket support [N2]. | Preferred durable local-Hermes deployment when user already has these prerequisites; service-supervise connector. No new domain purchase. |
| ngrok free | Assigned development domain, HTTPS, 1 GB/month outbound data, 20,000 requests/month and no endpoint timeout [N4]; HTTP endpoints support WebSockets [N5]. | Recommended normal-user setup if they already have/choose an account and no domain. Check quota before long calls. |
| Tailscale Funnel | Public TLS on tailnet DNS; ports 443/8443/10000; MagicDNS/HTTPS/policy requirements and non-configurable bandwidth limits [N6]. | Existing-tailnet alternative; end-to-end 35-minute WSS operation here is **UNCONFIRMED**. |

For us: prefer a stable named tunnel on an already approved host/domain, keeping Hermes local; an existing supervised HTTPS/WSS reverse proxy on the VPS is also suitable. Availability of either is **UNCONFIRMED**; no account access was attempted. If neither exists, coordinator chooses a provisioned route before R1. No service is installed or account created by this recommendation. No tunnel promises immunity to reconnects; Cloudflare warns that network updates can terminate WebSockets [N2].

Validate signatures before parsing trusted events or upgrading WSS. Twilio signs HTTP requests; use its SDK validator over the exact external URL and complete parameters/body as documented [S1,S2]. Classic validation requires the account Auth Token, not merely an API key secret [S2]; credential availability is **UNCONFIRMED**, to be resolved later through the approved secret route. Bind HTTPS/WSS URL reconstruction to configured public origin, not arbitrary forwarded headers. Test the documented voice/WSS URL normalization, including trailing slash behavior [S2]. Reject missing/invalid signatures, foreign account/call/session binding and closed sessions; bind the socket to a server-issued short-lived session reference. Do not put secrets in URLs or logs. Signatures alone do not prevent replay: persist event identities/terminal state and make actions idempotent. Keep webhook routes public without browser login challenges; expose no Desktop/admin/secret endpoints. Signature-valid callbacks still cannot bypass outbound approval, destination checks or spend caps.

## 6. Emergency destination enforcement

Confirmed examples: US 911 [E1]; UK 999/112 [E2]; Australia 000/106/112 [E3]. These are examples, not an exhaustive international list. Worldwide emergency aliases and full-length emergency service numbers are **UNCONFIRMED** as a complete maintained registry. Do not ship a purportedly complete static list.

Proposed mandatory block policy:

- Explicit emergency deny set at minimum `911,112,999,000,106`; preserve leading zeros and reject these with punctuation, whitespace, URI wrappers, dialing prefixes or a country prefix. Reject *all short/service-code targets of six digits or fewer*, not only listed emergency codes. Also reject noncanonical, ambiguous, star/hash, extension/post-dial and arbitrary SIP targets in destination fields. Parse rather than stripping dangerous characters into a different number.
- Accept only canonical, validated full E.164 destinations in supported jurisdictions; initial release/test mode is US-only with the exact coordinator-approved Can-line allowlist. The Bora number is an explicit hard deny. Unsupported countries fail closed. Never infer safety from a number merely being long enough.
- Future international support requires reviewed country-specific emergency/service-code metadata, aliases and full-number denies before enabling that country. Until then international dialing is blocked. Never suffix-match `911` on an otherwise valid approved full number; that creates unrelated false blocks without solving emergency routing.
- One central `validate_outbound_destination` gate (proposed name, not existing code) runs before approval is displayed and again immediately before **every provider call creation**: business call, owner bridge leg, callback redial, retry, transfer/participant creation, verification target and generated TwiML `<Dial>` target. The adapter receives only a validated destination object bound to approval/task/role. Revalidation occurs after model/tool output and redirects. Forwarding targets are checked during later setup; automatic unknown redirect/transfer dialing is disabled.
- Mid-call DTMF is a separate approved IVR operation on an existing leg; it cannot accept a phone-number transfer instruction that bypasses the destination gate. No emergency call test, including a provider “test” dial, is authorized. Offline tests assert zero provider invocations for rejected values and every call-creation entry point.

There is no source implementation in this checkout; this specifies placement, not completed enforcement. User approval can never override the hard emergency/Bora denies.

## 7. Budget and remaining evidence

Official US list rates: local number $1.15/month; outbound $0.014/min; inbound $0.0085/min; Relay $0.070/min; Media Streams $0.0044/min; conference starting at $0.0018/participant-minute; AMD $0.0075/call [T5,T13]. Account-specific fees, billing rounding, taxes, verification charges, Hermes marginal inference cost, Fi/Spectrum charges and effective rates are **UNCONFIRMED**. Test plan allowances do not assert those items are free. Check rates and existing-account charges after authorization before committing spend.

The accompanying plan allocates **$7.4738** including number/setup estimates, plus **$2.00** tax/rounding allowance and **$3.00** controlled rerun allowance: **$12.4738 planned ceiling**, below $15. Keep **$5.00 (25% of $20) untouched** and another $2.5262 unallocated below the $15 operational threshold. No renewal, recording, premium speech add-on, separate model purchase or third-party call is included. Do not consume reserve automatically. Long hold testing keeps Relay active rather than assuming silence is unbilled.

Open risks: account addendum/feature availability; caller ID and Fi forwarding acceptance; speech hallucinations over music; quiet humans and recorded conversational greetings; Hermes response time/cost; transition/reconnect gaps; stale/replayed events; provider billing/tunnel limits; incomplete international registry. All runtime behavior remains **UNCONFIRMED** until evidence is collected. S2 delivers a plan, not a passed telephony spike.

## Official source register

Every entry below was accessed **2026-11-04**; the same access date applies to inline IDs and linked test plan/ADR references. Only public pages were used.

- T1 — [ConversationRelay TwiML](https://www.twilio.com/docs/voice/twiml/connect/conversationrelay).
- T2 — [ConversationRelay WebSocket messages](https://www.twilio.com/docs/voice/conversationrelay/websocket-messages).
- T3 — [Media Streams overview](https://www.twilio.com/docs/voice/media-streams).
- T4 — [Media Streams WebSocket messages](https://www.twilio.com/docs/voice/media-streams/websocket-messages).
- T5 — [US Voice pricing](https://www.twilio.com/en-us/voice/pricing/us).
- T6 — [ConversationRelay best practices: connection drops](https://www.twilio.com/docs/voice/conversationrelay/best-practices).
- T7 — [ConversationRelay onboarding](https://www.twilio.com/docs/voice/conversationrelay/onboarding).
- T8 — [Answering Machine Detection](https://www.twilio.com/docs/voice/answering-machine-detection).
- T9 — [Conference TwiML](https://www.twilio.com/docs/voice/twiml/conference).
- T10 — [Conference participant resource](https://www.twilio.com/docs/voice/api/conference-participant-resource).
- T11 — [Number TwiML: private pre-connect URL](https://www.twilio.com/docs/voice/twiml/number).
- T12 — [Dial TwiML](https://www.twilio.com/docs/voice/twiml/dial).
- T13 — [Conference pricing](https://www.twilio.com/en-us/voice/conference/pricing).
- T14 — [Call resource: create/update/status](https://www.twilio.com/docs/voice/api/call-resource).
- T15 — [Verified outgoing caller IDs](https://www.twilio.com/docs/voice/api/outgoing-caller-ids).
- G1 — [Fi call forwarding and caller ID](https://support.google.com/fi/answer/6201380?hl=en).
- N1 — [Cloudflare quick tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).
- N2 — [Cloudflare WebSockets](https://developers.cloudflare.com/network/websockets/).
- N3 — [Cloudflare named tunnel setup](https://developers.cloudflare.com/tunnel/get-started/).
- N4 — [ngrok free plan limits](https://ngrok.com/docs/pricing-limits/free-plan-limits).
- N5 — [ngrok HTTP/S endpoints](https://ngrok.com/docs/gateway/endpoints/http).
- N6 — [Tailscale Funnel requirements and limits](https://tailscale.com/docs/features/tailscale-funnel).
- S1 — [Twilio secure webhooks](https://www.twilio.com/docs/usage/webhooks/webhooks-security).
- S2 — [Twilio request signature validation](https://www.twilio.com/docs/usage/security).
- E1 — [US National 911 Program: calling 911](https://www.911.gov/calling-911/).
- E2 — [UK official emergency numbers](https://www.gov.uk/guidance/999-and-112-the-uks-national-emergency-numbers).
- E3 — [Australian Government emergency-number explanation, section 30](https://www.legislation.gov.au/F2015L00319/asmade/2015-03-19/es/original/pdf). Historical official document confirms these examples; worldwide/current routing completeness is not asserted.
