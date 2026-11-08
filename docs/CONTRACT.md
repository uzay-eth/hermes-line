# Build contract (coordinator: Kodcu, 2026-11-04)

Design sources: ADR 0002 (plugin architecture), ADR 0003 (telephony stack), docs/spikes/S1-S3.
Each lane writes only its own paths. A lane that needs a change in another lane's path or in this contract stops and
reports it to the coordinator.

## Layout and owners
| Path | Lane |
|---|---|
| `plugin/plugin.yaml`, `plugin/__init__.py`, `plugin/skills/hermes-line/`, `plugin/line/engine/`, `plugin/line/store/`, `plugin/line/server/` | L1 engine |
| `plugin/line/policy/`, `plugin/line/detect/`, `fixtures/hold-sim/` | L2 safety + detection |
| `plugin/desktop/`, `tools/pane-preview/` | L3 Desktop pane |
| `plugin/line/sms/` | L4 SMS/OTP |
| `tests/<lane>/` | each lane its own |
| `docs/`, `STATUS.md`, this file | coordinator (lanes may add `docs/lanes/<lane>.md`) |

Python >= the version Hermes v0.22.5 ships. Standard library first; any third-party dependency is declared in
`plugin.yaml` and named in the lane report.

## Interfaces L1 consumes (L2/L4 implement; L1 stubs them until merged)
- `line.policy.destinations.check_destination(raw: str, settings) -> DestinationDecision(allowed: bool, e164: str | None, reason: str)`.
  Hard denies (emergency, short/service codes, Bora [redacted], non-US) cannot be overridden by settings or approval.
- `line.policy.consent.ConsentStore`: `issue(action, task_id, binding: dict, ttl_s) -> token`;
  `consume(token, action, task_id, binding) -> bool` (single use; expired, reused or mismatched = False).
- `line.policy.settings.Settings` with defaults: disclosure line ON, recording OFF, quiet hours ON (21:00-08:00 owner
  time zone), one call per line, allowlist-only dialing. `Settings.load(dict) -> Settings` validates.
- `line.policy.quiet_hours.is_quiet(now: datetime, settings) -> bool`.
- `line.detect.HoldTracker` (stateful, fed one transcript utterance at a time) and
  `line.detect.classify(window: list[Utterance], context, ask_model: Callable[[str], str] | None) -> Detection(label, confidence, evidence)`,
  label in `human | hold_message | ivr | voicemail | unknown`. `ask_model` is injected (L1 passes the Hermes call
  session); tests use fakes.
- `line.sms`: sources (adb, gateway, fixture), `OtpMatcher`, `SecretText`, and
  `release_otp(task_id, consent_token, consent_store) -> SecretText | None`.

## Desktop REST and events (L1 serves, L3 renders)
- `GET /api/plugins/hermes-line/calls` -> `{revision: int, calls: [Call]}`.
- `Call = {id, task_title, direction: "outbound"|"inbound", counterpart_masked, state, started_at, duration_s,
  mode: "agent"|"owner", transcript: [{seq, speaker: "hermes"|"business"|"owner"|"system", text, ts}], summary: str|null}`.
  Transcript text is already masked (OTP, card-like runs).
- `state` in `awaiting_approval | dialing | ivr | on_hold | human_detected | ringing_owner | owner_connected |
  agent_talking | ended | failed`.
- `POST /api/plugins/hermes-line/calls/{id}/take_over`, `/say` (`{text}`), `/hang_up`; every POST body carries
  `{idempotency_key, revision}`; a stale revision returns 409.
- Event `plugin.hermes-line.call.updated` with `{id, revision}`; the pane re-fetches the snapshot.

## OTP delivery (coordinator decision 2026-11-04, after L4 audit)
- Sealed delivery only: the model never receives a code. `line_otp_read` (cleartext) stays disabled and is not
  documented or registered as a tool. Reason: Hermes core persists every tool result to state.db, FTS, events and
  webhooks with no ephemeral flag (docs/lanes/L4.md), so a cleartext result breaks "masked everywhere".
- Sealed targets: browser fill (`line_otp_fill`, L4, done) and a live call (`line_otp_to_call`, L4 builds the tool,
  L1 implements the sink): `line.engine.sealed.send_secret_to_call(call_id: str, secret: SecretText, mode: "dtmf"|"speak") -> bool`.
  The call transcript, events and Desktop snapshot record only `[verification code sent]`.
- Consent for an OTP release binds senders, window AND the destination (site origin for fill, call_id + counterpart for
  call); the owner approval prompt shows that destination.
- The set of released message ids is persisted by L1's store (survives restart): `line.store.released_otps` with
  `add(message_key) / contains(message_key)`.

## Desktop contract clarifications (coordinator, 2026-11-04, from L3 questions)
- Plugin id is `hermes-line` everywhere (package, catalog, `/api/plugins/hermes-line`, event prefix).
- `Call.state_since` (ISO 8601, UTC) is added: when the current `state` began. The hold timer uses it.
- `Call.mode` is the owner's chosen plan: `owner` = bridge me in, `agent` = Hermes finishes the call. Who speaks now is
  derived from `state`.
- `revision` is snapshot-level (from `GET /calls`); POSTs send it; events carry the new snapshot revision.
- Every `ts`, `started_at`, `state_since` is ISO 8601 UTC with offset.
- Masked spans in transcript text are bracketed uppercase tokens: `[CODE]` for OTP/verification codes, `[CARD]` for
  card-like runs, `[NUMBER]` for masked phone numbers. The pane renders any `[UPPERCASE]` token as a chip.

## Owner updates 2026-11-05 (via Vezir)
- Voice: Grok Voice (xAI realtime, Media Streams, pcmu 8 kHz) is the default speaking path, pluggable; ConversationRelay
  stays as the alternative. No voice-model session during IVR/hold. Hermes stays the brain via function tools.
  Owner: L5 `plugin/line/voice/`, `tests/l5/`. `XAI_API_KEY` optional.
- Webhook auth: Auth Token signatures preferred when present; fallback without it (path secret, per-call tokens,
  REST CallSid check with the API key). Owner: L1.
- Desktop: direction C (Signal) chosen. Owner: L3.
