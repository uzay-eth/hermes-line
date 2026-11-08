# ADR 0002: Native plugin, profile-owned call service and Hermes agent driver

Status: selected by S1 for implementation, with explicit production gates below.
Date: 2026-11-04.
Evidence: [S1 spike](../spikes/S1-hermes-integration.md).

## Context

The phone brain must remain the user's Hermes, with memory and tools available
mid-call. A separate prompt-only speech model does not satisfy the brief.
Catalog policy forbids core replacement and credential-store access. Hermes
v0.22.5 exposes native plugin registration, streamed Python agent turns, message
routing and Desktop extension surfaces. The live catalog has 18 rules; the local
pinned README has 17. Admission must follow the live policy at submission.

S1 ran the real agent loop against an offline SSE model fixture and exchanged
ConversationRelay-shaped text over a local WebSocket. Memory reached every model
request and ten fake calendar calls executed. This measures framework/transport
overhead, not actual LLM or telephone latency.

## Decision

1. Ship a native `plugin.yaml` + `__init__.py` package, a bundled namespaced skill,
   and `desktop/plugin.js`. Initially require Hermes >=0.22.5, the tested release.
   No core patches, private registry mutation, self-updater or bundled binaries.
2. Own the phone listener and task state in a profile-owned service, separate from
   a transient chat turn or Desktop pane. Start only when the service is explicitly
   enabled. Register bounded cleanup with `ctx.on_unload`; provide process-exit
   fallback and crash recovery. Elect one owner per profile/line, with explicit
   readiness/failure reporting to other processes. Callbacks require an always-on
   gateway/service process; a short-lived CLI query cannot provide that guarantee.
3. Use a dedicated serialized call session, preserving the initiating profile,
   approved task briefing, memory, effective tool grants and model route. The
   measured fast driver uses the documented `AIAgent.run_conversation` embed with
   streamed callbacks and retained history. Never concurrently reuse or modify the
   initiating chat's active agent object. Initialize once and keep it warm.
4. Define the driver interface around `start`, `turn(text, emit)`, `interrupt` and
   `close`. Direct embedding is usable only when profile identity, subscription
   routing and approval transport are carried through documented APIs. Otherwise
   use the public gateway session protocol, which owns those responsibilities.
   This fallback is part of the chosen design, not permission to reconstruct
   private credential factories. No new model subscription or API key.
5. Stream visible output only. Production ingress uses a maintained HTTP/WS
   implementation, authenticated Twilio requests, call-ID correlation and bounded
   queues. On interruption, cancel the current generation, discard stale chunks
   and reconcile the history with text actually spoken. The dependency-free S1
   wire fixture is disposable test code, never the public server.
6. Capture origin profile/session/chat/thread at task creation. Use a bound
   `post_message` handle for gateway summary delivery where supported; otherwise
   use consented `inject_message` targeting the original session, understanding
   that injection starts a new turn. Persist an idempotent delivery outbox.
7. Implement explicit single-use owner consent for outbound calls and OTP release,
   bound to action/task/destination or OTP task/sender/time window. Generic Hermes
   tool approvals remain in force but are insufficient alone because YOLO/off
   and cached approvals can suppress prompting. Unattended/no-asker means no
   consent. Never accept phone speech or model output as owner authorization.
8. Keep OTP values out of persisted history, transcripts, logs, summaries and UI
   broadcasts. Audit the core tool-result persistence path before implementing the
   one approved cleartext tool delivery. Until proven, OTP release stays disabled.
9. Desktop uses a `panes` contribution plus namespaced `ctx.rest` actions/snapshots.
   Push sanitized invalidation/revision events with `broadcast_plugin_event` and
   `ctx.onEvent`. Optional namespace socket plus polling fallback handles socket
   unavailability; OAuth remotes and secondary plugin-host profiles have limits.
   Bind controls to call ownership/revision and idempotency keys. Closing the pane
   must not terminate an active call merely as a renderer lifecycle side effect.
10. Honor ADR 0001: semantic decisions use the user's Hermes or deterministic
    rules; no TypeSafe/Jev service dependency or key is introduced by this lane.

## Rejected alternatives

| Alternative | Reason |
|---|---|
| Runtime monkeypatches of AIAgent/gateway/Desktop | Catalog rule 9/8 violations and competing plugins would overwrite each other. |
| `ctx.llm` as the phone brain | Public completion API lacks the required streaming agent/tool loop. Suitable only for bounded auxiliary decisions. |
| New subagent for every utterance | Repeated startup, fragmented history and lifecycle API without resume-after-completion. |
| Read subscription tokens and call a provider directly | Violates credential routing and bypasses Hermes tools/memory semantics. |
| Spawn `hermes chat` for every utterance | Cold startup and transport overhead; brittle cancellation/context; unattended approval risks. |
| Borrow the original chat's private agent object | Concurrency, history and callback corruption; no stable cross-surface contract. |
| Use Desktop renderer or its pane as server owner | Closing/reloading UI would break callbacks/calls; renderer SDK is not a telephony host. |
| Put Twilio on the Desktop owner-authenticated namespace | Twilio cannot authenticate as the Desktop owner; ingress needs its own signature/call admission boundary. |
| Trust generic tool approval as per-call consent | YOLO/off/session/permanent decisions can bypass a new human prompt. |
| Declare production readiness from the stub latency or validation exit code | Neither verifies real inference/phone latency nor a clean dependency install. |

## Consequences and gates

The API shape is concrete enough to implement service, call-state, ingress and
Desktop lanes. Do not enable phone actions until all applicable gates pass:

- Live profile/model/subscription/tool inheritance and approval transport on the
  chosen driver; real route latency under the existing subscription only.
- Catalog review of `run_agent` import: programmatic docs recommend it, while the
  plugin linter's allowlist omits it. Use gateway driver if this cannot be resolved.
- Single-owner contention, unload/reload, restart and dropped-socket tests;
  authenticated ingress; S2's telephony/hold/barge-in checks after TWILIO-GO.
- Explicit consent and OTP persistence/redaction tests on CLI/gateway/Desktop.
- Clean dependency preparation and full catalog validation at the submission pin.
  S1's dependency-free plugin passes admission checks with exit 0, but offline
  core-environment preparation fails on uncached setuptools/wheel resolution.
  The validator catches that exception and continues. Preserve that distinction.

No remote publication, phone spend, tunnel or owner configuration change is
approved by this ADR. The coordinator owns any subsequent owner-decision request.
