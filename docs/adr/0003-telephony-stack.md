# ADR 0003: Text-first telephony with ConversationRelay and conference takeover

Date: 2026-11-04. Status: **technical recommendation for coordinator review; live execution blocked on TWILIO-GO and any unmet owner prerequisites**. This is not acceptance of provider terms or an owner-decision block.

## Context

Hermes Line needs IVR navigation, speech interruption, 30+ minute quiet holds and owner takeover while the user's Hermes agent remains the brain. Reliability has priority over theoretical latency and minimum transport cost. The verified brief hash is `346bbac78d9e7054ca55985ed0181b13449d2b7fa359cb593d870ad9d3eb167d`.

This ADR uses the official public sources in [S2 telephony](../spikes/S2-telephony.md#official-source-register), all accessed **2026-11-04**. Runtime behavior has not been tested. Unverified facts are **UNCONFIRMED** there and below. No call/API/account access occurred.

## Decision

Use Twilio ConversationRelay as the speech boundary and a plugin-owned text adapter to the existing Hermes agent. Relay handles STT/TTS; Hermes owns reasoning, memory, tools and task state. Use documented native `sendDigits` for IVR and interruption events to reconcile speech history ([Twilio protocol](https://www.twilio.com/docs/voice/conversationrelay/websocket-messages), accessed 2026-11-04). Persist turn/call state, suppress replayed side effects, and opt into bounded connection recovery ([Twilio best practices](https://www.twilio.com/docs/voice/conversationrelay/best-practices), accessed 2026-11-04). End-to-end latency and recovery on our deployment remain **UNCONFIRMED**.

Keep Relay active and silent throughout hold. Detect repeated transcript prompts deterministically, then use the existing Hermes agent for ambiguous IVR/human/voicemail classification. Require contextual conversational evidence before owner ringing. Preserve UNKNOWN when evidence is insufficient. Async AMD is optional initial-answer evidence, never the queue monitor ([AMD](https://www.twilio.com/docs/voice/answering-machine-detection), accessed 2026-11-04).

Use a conference only at accepted takeover. Ring the separate owner Spectrum line while Relay stays on the business; private whisper/press-1 consent precedes moving the existing business Call SID into the conference. Hermes stops speaking on takeover. Require all-leg cleanup and explicit failure states. Conference supports participant lifecycle controls ([Conference](https://www.twilio.com/docs/voice/twiml/conference), accessed 2026-11-04); transition gap and return-to-agent behavior are **UNCONFIRMED** until tested. No post-takeover transcript is claimed.

Keep Fi mobile service and SMS on the Pixel; verify Fi as outgoing caller identity. Fi officially offers external forwarding, Always forward and simultaneous ringing; busy/no-answer forwarding is **UNCONFIRMED** from official help ([Google Fi](https://support.google.com/fi/answer/6201380?hl=en), accessed 2026-11-04). Recommend Always forward to one new Twilio local number, bridge to Spectrum, and prohibit forwarding loops. If forwarding cannot be verified, report the explicit Twilio callback-number alternative before changing the intended experience.

Prefer a stable Cloudflare named tunnel for us if an approved host/domain is already provisioned (availability **UNCONFIRMED**); recommend ngrok free to normal users without their own domain who choose an account. Quick tunnel is an attended development fallback. Provider facts/limits and alternatives are in S2 section 5. Authenticate HTTPS and WSS with Twilio SDK signature validation and configured external origin; classic signing requires the account Auth Token ([Twilio security](https://www.twilio.com/docs/usage/security), accessed 2026-11-04). An API key alone is insufficient for that validation. No new tunnel account or credential is authorized here.

Implement a single fail-closed outbound destination gate shared by every call/bridge/retry/transfer path. Reject emergency and short/service-code destinations before approval and again at provider dispatch. Initial dialing is US-only and restricted to Can's approved full-number allowlist; worldwide dialing remains disabled until country-specific safety coverage is reviewed. The Bora hard deny is unconditional. See S2 section 6 for the concrete policy and official emergency examples.

## Rejected/deferred alternatives

| Alternative | Reason |
|---|---|
| Bidirectional Media Streams + realtime speech model | Raw audio gives acoustic features, but adds speech-model credentials/cost, codecs, turn cancellation and text-brain synchronization. Outbound DTMF over that stream is unsupported ([Media Streams](https://www.twilio.com/docs/voice/media-streams), accessed 2026-11-04). No separate model is approved. |
| AMD as hold/human detector | Initial-greeting classification is bounded and fallible; it does not observe a human arriving after 30 minutes. |
| Keyword-only human detection | Recorded “hello” and queue announcements can trigger false owner rings. Use context plus confirmation and retain uncertainty. |
| Continuous acoustic monitoring alongside Relay | Concurrent feature compatibility, cost and operational value are **UNCONFIRMED**. Raw audio is absent from Relay's text protocol; do not pretend it is available. |
| Always-on conference | Extra participant charges/control complexity before takeover. Start at handoff instead. |
| Direct Dial for all bridging | Simpler and has a called-party whisper URL, but independent participant lifecycle/control is preferable here. Retained as fallback design, not automatic substitution after failure. |
| Suspend speech recognition on long holds | Saves a possible fee but risks missing the first live utterance; no validated wakeup path. |
| TypeSafe/Jev shipped dependency | ADR 0001 records no new paid key/dependency for catalog users. Use existing Hermes/deterministic decisions; no provider change. |
| Conditional Fi forwarding assumption | Busy/no-answer mode lacks confirmation on the official help page; use documented forwarding or explicit callback identity. |
| Unqualified global emergency deny list | No complete maintained worldwide/full-number registry verified. Reject shortcodes and unsupported jurisdictions rather than claiming global coverage. |

## Consequences, prerequisites and evidence

Calculated base US outbound cost is $0.084/min including Relay; a 35-minute hold is $2.94 before inference/tax. Conference handoff adds both PSTN legs and participant charges. Sources: [US prices](https://www.twilio.com/en-us/voice/pricing/us), [conference prices](https://www.twilio.com/en-us/voice/conference/pricing), accessed 2026-11-04. Effective account costs, taxes and billing rounding are **UNCONFIRMED**; budget allowances are estimates.

[The test plan](../spikes/S2-real-call-test-plan.md) schedules eight voice scenarios and two owner verification flows, plus offline labeled transcript/audio fixture work. It allocates $7.4738 including setup/number estimates and caps planned spending including allowances/reruns at $12.4738, below $15; $5 of the $20 cap remains untouched reserve. No paid alternate realtime model or recorded-call storage is included.

Owner steps through the coordinator: provide Spectrum target privately; review any unmet Relay addendum prerequisite; answer Fi caller-ID verification; verify/set Fi forwarding to the new number; participate in an approved test window. Twilio requires the AI/ML addendum for Relay ([onboarding](https://www.twilio.com/docs/voice/conversationrelay/onboarding), accessed 2026-11-04); prior acceptance is **UNCONFIRMED**. Stop at that boundary for coordinator handling; no worker accepts it. If declined, reopen this ADR rather than silently adopting another paid speech provider.

Required evidence: recorded hold simulation explicitly labeled as such; per-call SID/duration, masked transcript and unavailable spans; interruption/digit/transition measurements; false/missed-human counts and confusion matrix; no-answer/business-hangup/replay cleanup. Until those pass, this is a source-backed design recommendation with **UNCONFIRMED** runtime reliability, not a completed working telephony integration.
