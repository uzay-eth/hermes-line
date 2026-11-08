# L1 round 3 addendum (coordinator, from Can 2026-11-05)

Add after your round 3 items, same thread, same limits:

Webhook and stream authentication without the Twilio Auth Token. Can cannot reach the Twilio Console for now, so the
Auth Token is not available; the API key (SK + secret) is. Keep Auth Token signature validation as the preferred mode
when a user has it (`TWILIO_AUTH_TOKEN` optional). Without it, build a fallback mode and document it honestly:
- an unguessable per-install path secret (at least 128 bits, generated and stored by the plugin, constant-time compare)
  in every webhook and stream URL, HTTPS/WSS only;
- per-call one-time tokens for each stream/relay URL that the plugin itself returns in TwiML;
- a check against the Twilio REST API with the API key that the CallSid exists on this account and its from/to match
  the task, before any side effect or owner ring (cache per call);
- reject and log (masked) anything that fails; a test per rejection shape, zero side effects.
State in `docs/lanes/L1.md` what the fallback does not protect against compared with signatures.
Note: L5 (new lane) is building a Media Streams + realtime voice path in `plugin/line/voice/`; it will list the
integration points it needs from you. Do not build voice code yourself.
