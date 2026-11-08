# S1 — Hermes integration architecture spike

Date: 2026-11-04. Scope: offline architecture and prototype, not a phone integration.

## Verdict

A native plugin can own a long-lived HTTP/WebSocket listener and invoke the real
Hermes agent loop without replacing core. The lowest-overhead documented Python
entry point is `run_agent.AIAgent.run_conversation`, with streaming callbacks,
retained history, memory and registered tools. This spike demonstrates that path
with an offline model fixture. It does **not** establish subscription-model or
end-to-end telephone latency, or prove that a bare `AIAgent()` automatically
inherits every setting of the chat that requested a call.

Choose a native Python plugin with an explicitly enabled, profile-owned call
service, one serialized Hermes session per call, and a Desktop SDK pane. Keep the
agent driver behind an interface: the direct Python embed is the measured fast
path; use the documented gateway session protocol for host-owned routing and
approval transport when direct embedding cannot preserve them. Never reach into
the active chat's private agent fields or replace its callbacks. The remaining
integration gates below must be resolved before enabling real calls.

## Evidence and boundaries

- `docs/BRIEF.md` SHA-256 verified before work:
  `346bbac78d9e7054ca55985ed0181b13449d2b7fa359cb593d870ad9d3eb167d`.
- Read project `AGENTS.md` and ADR 0001. Graft map/ask returned an empty graph;
  direct source inspection followed. No graph rebuild: no existing application
  code changed and allowed writes exclude `graft/`.
- Read-only Hermes tree: `/Users/mac/.hermes/hermes-agent`, v0.22.5, commit
  `bf0203a794a07aac3ff764972ab9087baa4e4f4e`.
- All abbreviated `file:line` references below are in that tree. They identify
  source evidence, not proposed files in Hermes Line. Local documentation spans
  are labeled as docs; implementation spans substantiate API behavior.
- Live official sources checked: [docs index](https://hermes-agent.nousresearch.com/docs),
  [plugin guide](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins),
  [submission guide](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/catalog-submission),
  [canonical catalog rules](https://github.com/NousResearch/hermes-agent/blob/main/plugin-catalog/README.md),
  [Desktop SDK](https://hermes-agent.nousresearch.com/docs/developer-guide/desktop-plugin-sdk),
  [programmatic integration](https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration),
  [agent loop](https://hermes-agent.nousresearch.com/docs/developer-guide/agent-loop),
  [plugin LLM access](https://hermes-agent.nousresearch.com/docs/developer-guide/plugin-llm-access),
  [subagent lifecycle](https://hermes-agent.nousresearch.com/docs/developer-guide/subagent-lifecycle-api).
  Live rules have clarifications absent from the pinned local README; live admission
  policy wins at submission. Recheck against the eventual pinned release.
- No phone/Twilio API requests, purchases, new keys, gateway restarts, owner profile
  changes, pushes, or source edits in Hermes. Only non-secret model/provider names
  were read from the owner's config; no credential stores were inspected.
- Design routing: installed design skills are listed in the session, but none was
  loaded because this is backend architecture, not a frontend design brief or pane
  implementation. Impeccable/Taste/Emil/native-motion checks are deferred to the UI
  lane. No required architecture skill was missing; no delegation was used.

## 1. Plugin and catalog surfaces

### Manifest and registration

`plugin.yaml` plus `__init__.py:register(ctx)` is sufficient for the native plugin.
The validator requires nonempty `name`, `version`, `description`; other metadata
includes `author`, `license`, `homepage`, `tags`. Use a quoted version. Parsed
manifest fields include `kind` (standalone here), `requires_hermes`, `requires_env`,
`provides_tools`, `provides_hooks`, `manifest_version` (default 1), `api_version`,
`requires_plugins` (advisory ordering, not hard installation), `python_dependencies`,
`config_schema`, `emits`, `listens`, and `provides_locales`. `source`, `path`, `key`,
`portable`, and skill namespace are loader bookkeeping, not user-authored identity
or authority. Additional surfaces include `provides_middleware`, `requires_auth`
and `oauth`; not all are fields of the parsed dataclass.
Sources: `hermes_cli/plugins_manifest.py:347-400`,
`hermes_cli/plugin_validate.py:101-112,164-185,188-205,430-451`,
`hermes_cli/plugin_provider_requests.py:112-161`.

`ctx.register_tool(name, toolset, schema, handler, check_fn=None,
requires_env=None, is_async=False, description="", emoji="", override=False)`
registers a scoped tool. Handler shape is `(args, **kwargs)`; accept injected
context rather than assuming a CLI-only call. Return a JSON string for this
prototype. Use a unique namespace and no overrides. Registration leases and
`ctx.on_unload` handle cleanup. Sources: `hermes_cli/plugins.py:462-499,415-434`.

Bundle `skills/<name>/SKILL.md`, then call
`ctx.register_skill(name, path_to_skill_md, description=...)`. It appears as
`<plugin-name>:<skill-name>`, read-only, while enabled; it is not copied into the
owner's skills directory. Source: `hermes_cli/plugins_content.py:23-52`.

Declare credential variable names in `requires_env` (strings or mappings with
`name`; UPPER_SNAKE_CASE). Configuration can expose a secret field using
`{type: secret, env: HERMES_LINE_TWILIO_AUTH_TOKEN}`. Read through
`agent.secret_scope.get_secret(...)`, inside the correct profile context.
A missing scoped secret must never fall back to another profile. With no scope
in a multiplexer, the helper raises; a standalone process may use its environment.
The manifest is not permission to read a credential file. Source:
`agent/secret_scope.py:230-261`; local docs
`website/docs/developer-guide/plugins/index.md:999-1017`.

For plugin-originated subscription HTTP calls, declare `requires_auth` and use
`credentialed_provider_request`; it attaches host credentials, restricts origins,
and does not follow redirects. Third-party OAuth belongs under `oauth`, with
HTTPS authorization/token endpoints and API-owned hosts. A Hermes agent turn is
preferable to manually constructing provider requests for the phone brain.
Source: `hermes_cli/plugin_provider_requests.py:134-182`.

Two kinds of capability metadata must not be confused: runtime consent IDs in
`plugin.yaml:capabilities` are declarations, not grants; registration inventories
are `provides_tools/hooks/middleware`. Catalog entry `capabilities` is a separate
listing summary. The v0.22.5 probe checks the inventories, not every possible
runtime behavior. Source: `hermes_cli/plugins_manifest.py:372-375` and
`hermes_cli/plugin_validate.py:402-453`.

Use `requires_hermes: ">=0.22.5"` in this probe and eventual entry until earlier
versions are tested. The loader skips an incompatible version before import
(`hermes_cli/plugins_loader.py:486-492`); validation parses the requirement but is
not a cross-version compatibility test (`hermes_cli/plugin_validate.py:81-129`).
The live rule also enforces the catalog floor at installation/update; use the
same floor in both manifest and entry.

### All 18 admission rules

This checklist paraphrases the canonical policy, checked live; source spans are
`plugin-catalog/README.md:14-149` for rules 1–17 in the pinned checkout.
Rule 18 exists in the live canonical README and is absent locally. Detailed Desktop and
credential constraints above also apply.

| Rule | Requirement and implication for Hermes Line |
|---|---|
| 1 | Maintainer-reviewed, human-merged PR only; no automatic listing. |
| 2 | Pin a full 40-character commit SHA; no branch, tag or abbreviated SHA. |
| 3 | No self-updater, remote replacement JS, or code-fetch-and-replace path. Updates use catalog SHA bumps and Hermes update. |
| 4 | Every SHA bump is a new reviewed PR covering the upstream diff. |
| 5 | Repository owner/major contributor submits, or a maintainer performs a reviewed community sweep. Sweeps validate/scan pins and English-first UI; authors retain removal/edit control. |
| 6 | Declared capabilities must match actual registrations; no undeclared expansion. |
| 7 | Admission runs the install security scan. Dangerous blocks; reviewers must examine caution warnings. Reviewed pins may install with caution without another prompt. |
| 8 | Desktop uses SDK surfaces only. No renderer/browser prototype patches, eval/Function, remote/private dynamic imports, script injection, private stores, or core DOM manipulation. Own library classes are allowed. Raw bridge calls and suspicious core-markup selectors also warn in live policy; missing SDK hooks must be requested upstream. |
| 9 | No replacing/wrapping/rebinding core functions, methods, attributes or tables. Undocumented imports and private method calls warn in live policy. Request missing hooks instead. |
| 10 | Plugin owns dependency security policy; Hermes exact pins cannot move. Bound compatible versions, avoid unjustified newest floors; release quarantine is recommended. Core's three-day rule does not automatically protect plugin dependencies. |
| 11 | Credentials only through Hermes secret/auth routes; no credential-file, browser, keychain or vendor-login scraping, token refresh/write, or vendor-client impersonation. OAuth endpoints/hosts must match the service. |
| 12 | Preserve approval guards; no autoapproval or child-process bypass. Human waits must fail or time out unattended. |
| 13 | README and PR disclose external services, outside-data reads, shell commands, background services and credential storage. Telemetry/reporting requires opt-in. |
| 14 | Truthful released SemVer floor, same in native manifest and entry; matching code/version; dependencies compatible with core. Live policy gates install/update; portable packages need the floor in the entry. |
| 15 | Changes to bundled plugins go upstream, not competing skins/forks or vendor-lookalikes under Nous branding. |
| 16 | One listing per lineage. Material forks require distinct capability/architecture or an unanswered upstream feature PR for 30 days; disclose origin. Cosmetic rebrands are declined. |
| 17 | Reviewable source only: no bundled native/WASM binaries, bytecode or hidden executable archives. Native exceptions require own public source build and GitHub provenance tied to the reviewed pin. Setup downloads must be official, checksum-pinned and disclosed. |
| 18 | Browser-vault backends are vendor-owned verified listings or Nous official listings only, enforced by host install provenance. Local/Git/project/pip installs cannot waive this. Not applicable to our phone plugin. |

Entry preparation additionally needs a public HTTPS cloneable repository, one
catalog YAML with a valid slug, full SHA, quoted version, description, maintainer,
compatible floor and accurate capabilities; optional monorepo `subdir` must be a
safe relative path. UI requires screenshots; rule-13 disclosures belong in README
and PR. CI validates the exact pin. Repository publication and submission remain
owner decisions. Sources: local README entry schema after line 163 and the live
[submission guide](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/catalog-submission).

### What validation actually checks

`hermes plugins validate PATH --install-deps` calls `pm.sync_venv` with the candidate
plugin and probes using the selected dependency interpreter. It does not merely
run pip in the shell's venv. **Dependency-preparation exceptions are printed but
not made report failures**; the subsequent checks determine exit status. This is
why exit 0 alone cannot prove a clean installation. Source:
`hermes_cli/plugins_cmd_catalog.py:778-817`.

Checks include YAML mapping/required fields, version-spec syntax, config schema
shape/types, env names, OAuth shape, a loadable entry, PEP-508 dependency syntax,
registration under an isolated recording context, undeclared tools/hooks/
middleware, built-in tool collisions, trusted-inbound declarations, install
security scan, core overrides, private imports, Desktop surface and language
packs. It also reports plugin-host isolation readiness. Source:
`hermes_cli/plugin_validate.py:497-558,591-630`.

The capability probe substitutes `RecordingContext`, runs `register`, and treats
other real context methods as no-ops. It does not exercise our tool, server,
shutdown, approval, UI or voice behavior. It errors on undeclared registrations,
but unused declared names warn. Source:
`hermes_cli/plugin_validate.py:227-268,320-326,342-371,430-451`.

## 2. Long-lived HTTP and WebSocket service

Yes, ordinary Python plugin code can run a service thread while its hosting
process lives. `register(ctx)` is synchronous and has a load deadline, so it must
return promptly. `ctx.spawn_task` requires an already running asyncio loop; do not
assume one exists in `register`. Use a dedicated thread/loop for the production
server, or mount Desktop-facing routes in the documented backend namespace.
Sources: `hermes_cli/plugins_loader.py:106-147,474-549`,
`hermes_cli/plugins.py:415-434`.

| Host | Start | Stop / limitation |
|---|---|---|
| Classic CLI / interactive process | Enabled plugin discovery calls `register`; lazily start after explicit enablement, or start with service config. | `on_unload` on disable/reload; `atexit` best effort; process exit ends the listener. A one-shot chat command is not an always-on callback host. |
| Messaging gateway | Gateway startup discovers enabled plugins; service can live independently of a particular agent turn. | Same plugin unload contract. Process death ends calls; no promise that an async cleanup runs under forced exit. |
| Desktop | Python half runs in the serving Hermes process/plugin host, not the renderer. Enable Python plugin separately from renderer contribution. | Closing a pane does not own the phone service. Backend exit/reload does; profile changes must not cross ownership. |

Implementation evidence: `gateway/run_startup.py:1076-1081`,
`gateway/run.py:2241-2242`, `tui_gateway/agent_factory.py:354-355`,
`hermes_cli/plugins_loader.py:498-531`,
`hermes_cli/plugins_ledger.py:203-249`. The hook inventory at
`hermes_cli/plugins.py:108-193` has session/stream hooks; it is not a universal
process startup/shutdown contract. Plugin host mode may move code to a child
process. Do not equate the plugin's lifespan with every UI/client lifespan.

Prototype: `settings.start_server: true` starts the listener during register;
default false prevents validation/discovery from binding a public service. It
registers cleanup before starting and uses an ephemeral loopback port. Tests
prove real manager load/unload, not just a mock context. Production needs a
single-owner lease per profile/line so CLI, Desktop and gateway do not each bind
or answer the same line; persist call/task state and recover conservatively after
crashes. A lock alone must not make a losing process silently claim readiness.

Twilio needs a separate authenticated HTTPS/WSS ingress. The Desktop backend's
native/authenticated `/api/plugins/<id>` namespace is for owner UI, not a webhook
that can accept a Twilio caller's credentials. Keep Twilio signature validation,
call/session correlation and replay checks on ingress. No tunnel selected or
started here. The prototype `/webhook` always refuses requests; the minimal WS
fixture is not suitable for exposure.

## 3. Call brain and latency

### Fast path and alternatives

The documented Python embed avoids a new process and JSON-RPC hop per utterance:
construct a dedicated `AIAgent` once, serialize turns on its worker, call
`run_conversation(utterance, conversation_history=history, stream_callback=emit)`,
and carry `result['messages']` into the next turn. `stream_delta_callback` is also
a constructor option. The callback receives visible text; it is distinct from
reasoning callbacks. Send nonempty visible chunks immediately and send `last`
only once the turn completes. Sources: `run_agent.py:236-280`,
`agent/turn_facade.py:22-34`, `agent/stream_delivery.py:22-43` and the live
[programmatic guide](https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration).

This is the lowest-hop supported candidate, **not an experimentally proven winner
against every transport**. `AIAgent` loads tools and memory
(`agent/agent_init.py:1096-1120,1299-1377,2360-2377`). The dedicated call session
must use the initiating profile's model/provider, SOUL, effective tool grants,
working directory and memory scope, plus an explicit task briefing. It is the
user's Hermes runtime with separate conversational history, not concurrent reuse
of the initiating chat's object. Never call that object from the phone thread
while its original turn is active. `skip_context_files=True` is only a benchmark
setting, not the proposed production identity policy.

A bare constructor does not supply all gateway identity or approval plumbing:
they are explicit constructor parameters (`run_agent.py:250-279`), and provider
assignment occurs at `agent/agent_init.py:2314-2335`. Correct route inheritance,
subscription refresh and multiplexed context are **not demonstrated** by the stub.
A deployment that cannot preserve those through documented APIs should use the
public gateway session protocol (`session.create`, `prompt.submit`, events,
`session.interrupt`) so the host owns construction, routing, memory and approvals.
Do not implement a private provider factory or read authentication files to make
the Python embed work. The catalog import linter currently warns on `run_agent`
even though the programmatic guide documents it
(`hermes_cli/plugin_validate_private_imports.py:27-64`): resolve that warning with
upstream/reviewer evidence before shipping, or select the gateway driver.

`ctx.llm.complete/acomplete` are host-credentialed completions, not a streaming
agent/tool loop (`agent/plugin_llm.py:427-495`); useful for bounded auxiliary
classification, not the phone brain. `ctx.subagent_lifecycle` supervises a fresh
child, requires an active parent, and offers launch/status/wait/cancel/steer/result;
there is no public next-completed-turn/resume API. Finished handles cannot be
steered. Do not spawn a fresh child per utterance or poll terminal results as a
speech stream. Sources: `agent/subagent_lifecycle.py:300-358`, local docs
`website/docs/developer-guide/subagent-lifecycle-api.md:8-17,38-73`.

For production, barge-in needs cancellation plus a generation ID that drops stale
outbound chunks, and history must reflect what the caller actually heard. The
fixture only implements complete prompt/text exchanges. It does not test
interrupts, speech timing, DTMF, reconnect, retries, or callback matching. Wire
shape was checked against [Twilio's WebSocket reference](https://www.twilio.com/docs/voice/conversationrelay/websocket-messages).

### Measurement

Mac arm64, macOS 27.3; Hermes Python 3.14.9; pinned source above. Owner default
model/provider metadata: `claude-opus-5-5` / `anthropic`. The provider profile
advertises `auth_type="api_key"` (`plugins/model-providers/anthropic/__init__.py:49-55`),
but native credential resolution can also accept a token
(`agent/agent_init.py:744-762`). Therefore **subscription status is unverified**;
we did not infer it from a token or inspect one. No actual model inference was
requested. The permitted stub fallback was used.

Actual path: fake ConversationRelay WebSocket client → loopback plugin server →
real Hermes AIAgent → loopback OpenAI-format SSE fixture → real Hermes tool
execution when requested → streamed text back to fake relay. The model fixture
waits 20 ms before each response's first chunk and 5 ms between five text chunks.
A tool turn requires two model requests. Fake memory is present in every request;
10 fake calendar calls really execute. No real calendar is accessed.

Measured at client send until first **non-whitespace visible text**, and until
receipt of the final `last:true` marker. One cold turn, ten warm text turns and
ten warm tool turns; nearest-rank p95 (with n=10, the maximum). Construction is
reported separately. Tool search is off in scratch; production tool discovery
can add model rounds. All warm turns reuse the same agent and accumulating history.

| Case | First text median / p95 | Full turn median / p95 |
|---|---:|---:|
| Cold first turn (n=1) | 986.493 ms | 1014.219 ms |
| Warm text (n=10) | 28.438 / 32.526 ms | 59.808 / 65.470 ms |
| Warm calendar fixture (n=10) | 58.654 / 63.472 ms | 90.505 / 97.015 ms |

Agent construction: 320.930 ms, excluding Python import time. These are local
harness measurements with artificial model timing, **not LLM TTFT, reasoning
quality, production calendar latency, STT/TTS latency or telephone latency**.
The cold outlier is retained; no claim is made about its precise cause. Raw
samples: `spikes/s1-proto/evidence/latency.json`.

Commands from repository root:

```sh
sh spikes/s1-proto/offline.sh spikes/s1-proto/lifecycle.py
sh spikes/s1-proto/offline.sh spikes/s1-proto/benchmark.py
sh spikes/s1-proto/offline.sh hermes plugins validate spikes/s1-proto --install-deps
```

The wrapper invokes the installed Hermes CLI `main` for the last command, without
patching the validator. It supplies a throwaway home, an empty inherited
credential environment, and a macOS sandbox denying writes outside scratch and
networking except loopback. `/dev/null` is permitted. `HERMES_DATA_DIR_SUFFIX`
keeps `get_default_hermes_root()` from collapsing this nested scratch home to
`~/.hermes` (`hermes_constants.py:93-101`); `HERMES_HOME` alone is insufficient
here. Lazy startup repair is disabled during bootstrap; explicit validation may
then prepare dependencies only inside scratch.

Scratch preparation used a `venv --without-pip` from the existing Hermes Python,
read-only access to its installed site-packages, copy-on-write copies of the
existing pinned Python/uv tools and the Hermes uv cache, and a scratch config
selecting the fixture/custom route with fake `memories/MEMORY.md`. `offline.sb`
contains default allow, a file-write deny outside the S1 scratch subtree, a
network deny with localhost exceptions, and a `/dev/null` write exception.
No downloaded dependencies were obtained. Harness paths are Mac-specific and
pinned in `run.py`; run inside the prepared scratch environment on this Mac.

## 4. Results and approvals

Capture initiating profile plus session key and platform/chat/thread origin at
task creation. Never infer the recipient from Desktop focus after the fact.

`ctx.post_message` / `apost_message` posts to the bound gateway conversation and
returns a `PluginMessage` handle. Capture that handle while the tool/command has
its session context, then edit it with the final summary after the call. Check
`ok`, `error`, `pending`; posts without a bound conversation or live gateway are
refused. The handle retains its target for later edits. Sources:
`hermes_cli/plugins.py:615-624`, `hermes_cli/plugin_messages.py:25-85,150-195`.
Do not assume this is a universal CLI transcript-insert API.

For CLI/TUI/Desktop and routed gateway turns, `ctx.inject_message(summary,
session_key=origin_key)` can ask the original session to process the outcome.
This queues a **new user-like turn**, not an assistant summary bubble. In the
classic CLI it uses the pending/interrupt queue; outside that path it requires
`allow_gateway_injection: true` and an owning session or origin in the same
profile. `True` means accepted, not delivered. Use explicit correlation IDs and a
persistent outbox to retry only undelivered outcomes without duplication. Sources:
`hermes_cli/plugins_injection.py:12-69`, `hermes_cli/plugins.py:596-603,632-640`.
No injection consent was added to the owner's configuration in this spike.

Generic tool escalation is a `pre_tool_call` directive `{action: "approve", ...}`;
Hermes routes it through its human gate (`tools/approval.py:1121-1145`). **It does
not meet the brief's every-call consent requirement by itself**: YOLO, approvals
off, and session/permanent approval can bypass a prompt
(`tools/approval.py:968-1018`).

Use a separate plugin consent state machine. A sync `/line` command can present
`ctx.ask_choice` through the host; Desktop can show an explicit owner action via
SDK UI and authenticated backend. Store an expiring, single-use grant bound to
profile, owner session, normalized destination, task, action and request nonce.
Every outbound execution atomically consumes its grant; the phone interlocutor
and the model cannot mint one. Timeout, cancellation, stale identity, a background
hook or no user surface means no grant. `ask_choice` returns `None` without an
asker or on an async event loop (`hermes_cli/plugin_choices.py:81-96`). UI approval
is an extra requirement, not a substitute for Hermes's existing tool guards.

OTP release needs a separate grant bound to approved task, sender/time window
and consuming tool invocation. Keep the code out of summary, transcript, approval
text, errors, broadcasts and durable logs. The approved tool result is the single
intended cleartext delivery. **Core tool-result/session persistence must be audited
before enabling OTP**: ordinary tool registration alone cannot guarantee this
exception stays out of saved history. The prototype contains no OTP code or
release path. The caller's untrusted speech must never approve either action.

## 5. Desktop pane and live updates

Ship `desktop/plugin.js` in the unified Python package. It default-exports
`{id, name, register(ctx)}` and imports from `@hermes/plugin-sdk` / React. Register
a `panes` contribution, `data` with `placement: 'right'` and an explicit width,
and a render function using the SDK-supported React primitives. The user can
move the pane. The pane owns a live call list, masked transcript and Take over /
Say this / Hang up buttons; it does not own telephony lifetime. Sources:
`apps/desktop/src/contrib/plugin.ts:111-155,181`,
`apps/desktop/src/contrib/types.ts:24-52`; example in local SDK docs at
`website/docs/developer-guide/desktop-plugin-sdk.md:246-280`.

Backend: `dashboard/manifest.json` names `plugin_api.py`, which exports FastAPI
`router`. Routes mount at `/api/plugins/<id>/`; `ctx.rest('/calls')` and POST
`/calls/<id>/actions` are profile-aware. Python enablement is separate from renderer
enablement. Proposed action payload includes call ID, expected revision, action
and idempotency key; server verifies ownership/state, and Say this never executes
model-supplied code. Take over must perform the approved bridge workflow; a UI
click alone is not evidence that a phone bridge succeeded. Source: SDK docs
`website/docs/developer-guide/desktop-plugin-sdk.md:1794-1832` and implementation
`apps/desktop/src/contrib/plugin.ts:146-155,363-367`.

Push sanitized revision/invalidation events with
`broadcast_plugin_event('hermes-line', 'call.updated', payload)` and subscribe via
`ctx.onEvent('plugin.hermes-line.call.updated', ...)`; fetch an authorized snapshot
on notification/reconnect. Events are process-scoped, not a cross-process message
bus. Avoid broadcasting full transcripts indiscriminately to all connected
windows. Source: `hermes_cli/plugin_events.py:15-27,50-88`.

`ctx.socket('/events')` is an alternative for connection-specific data. It
reconnects, but is a no-op on OAuth remotes and returns HTTP 501 for a secondary
profile's plugin host in the documented current implementation. Keep bounded
polling with `ctx.setInterval` as fallback, dispose subscriptions, and refresh on
profile switch. Sources: `apps/desktop/src/contrib/plugin.ts:125-155`, local SDK
docs `website/docs/developer-guide/desktop-plugin-sdk.md:1883-1932`. No pane was
built or visually reviewed in S1; this is the contract for the UI lane.

## Remaining gates and risks

1. Real subscription route and full conversational latency are unmeasured. Carry
   this benchmark forward as a lower-level regression fixture, not a phone SLO.
2. Prove host-equivalent route/tool/identity/approval inheritance for the Python
   driver; otherwise implement the documented gateway session driver. The
   `run_agent` import-linter warning is an admission review issue, not evidence
   of an actual core override.
3. Per-line owner election, process death/restart, 30-minute hold, cancellation,
   callback correlation, Twilio signatures and tunnel availability remain S2/core
   work. No real gateway/Desktop launch was tested; only the shared plugin loader.
4. Consent must survive YOLO/off/cached-approval settings without weakening Hermes
   guards. OTP persistence/redaction is a release gate. No new semantic model
   service: ADR 0001's own-Hermes/deterministic decision policy remains in force.
5. Validation exit 0 is weaker than clean dependency preparation on this Hermes
   version. The full output below exposes the offline preparation failure.
   Production dependencies and Desktop code need a fresh clean validation at the
   submission pin, with all warnings reviewed.

## Validation evidence

Final command and output follow (terminal trailing spaces trimmed). The plugin has no runtime dependencies;
all admission checks pass and exit status is 0. Core environment preparation
still attempted to resolve uncached build dependencies and failed under the
network-denying sandbox. This is **not** a successful cold install, and is not
reported as one. The lifecycle test independently imported and exercised the
real plugin in the existing installed Hermes dependency environment.

```text
$ sh spikes/s1-proto/offline.sh hermes plugins validate spikes/s1-proto --install-deps
⚠ install out of sync (ffmpeg: not installed or outdated; node: not installed or outdated; npm: not installed or outdated; ripgrep: not installed or outdated) — run `hermes pm install`
  → Installing Python dependencies…
  ✗ Installing Python dependencies failed; last 10 lines of output:
         Building hermes-agent @ file:///Users/mac/.hermes/cache/scratch/hermes-line-s1/installs/778930d1ee3bddda/environments/71a257b241614e36a67352e0ba6469a6/workspace
      error: Failed to build `hermes-agent @ file:///Users/mac/.hermes/cache/scratch/hermes-line-s1/installs/778930d1ee3bddda/environments/71a257b241614e36a67352e0ba6469a6/workspace`
        cause: Failed to resolve requirements from `build-system.requires`
        cause: No solution found when resolving: `setuptools==83.0.0`, `wheel`
        cause: Request failed after 3 retries in 6.0s
        cause: Failed to fetch: https://pypi.org/simple/wheel/
        cause: error sending request for url (https://pypi.org/simple/wheel/)
        cause: client error (Connect)
        cause: dns error
        cause: failed to lookup address information: nodename nor servname provided, or not known
dependency preparation failed: venv: uv sync exited 2:  file:///Users/mac/.hermes/cache/scratch/hermes-line-s1/installs/778930d1ee3bddda/environments/71a257b241614e36a67352e0ba6469a6/workspace`
  cause: Failed to resolve requirements from `build-system.requires`
  cause: No solution found when resolving: `setuptools==83.0.0`, `wheel`
  cause: Request failed after 3 retries in 6.0s
  cause: Failed to fetch: https://pypi.org/simple/wheel/
  cause: error sending request for url (https://pypi.org/simple/wheel/)
  cause: client error (Connect)
  cause: dns error
  cause: failed to lookup address information: nodename nor servname provided, or not known — retry, or run `hermes pm doctor`

✓ manifest — plugin.yaml parses
✓ manifest fields — name, version, description present
✓ requires_hermes — spec '>=0.22.5' parses
✓ config schema — shape valid
✓ requires_env — all entries UPPER_SNAKE
✓ oauth — not declared
✓ loadable — entry: __init__.py
✓ python dependencies — none declared
✓ capability probe — register() ran in isolation
✓ declared tools — matches registrations
✓ declared hooks — matches registrations
✓ declared middleware — matches registrations
✓ built-in tool collisions — no collisions
✓ security scan — safe
✓ no core override — no runtime rebinds of Hermes core
⚠ private core imports (catalog rule 9: public surfaces only; these can break on
any release): benchmark.py:14 run_agent.AIAgent; lifecycle.py:12
hermes_cli.plugins.PluginManager; run.py:16 hermes_bootstrap; run.py:18
hermes_cli.main.main. Use ctx.* / documented plugin APIs, or open an issue for
the seam you need.
◆ Isolation — runs in the plugin host (plugins.isolation: host)

Validation passed.

Exit status: 0
```

```text
PASS: real PluginManager load; one no-op tool; bundled skill; HTTP health; WS prompt/text; unload closes listener and removes tool
```

## Questions for the coordinator

No owner-decision block was posted. No decision is needed to use the offline
prototype and contracts. Before treating dependency installation as verified:

- A: authorize a narrowly scoped online dependency validation in scratch.
- B: supply a complete offline build/dependency cache and rerun in scratch.
- Recommendation: B, because it preserves the lane's offline limit while removing
  the cold-install evidence gap. A requires coordinator/owner authorization.

Engineering follow-ups (not owner choices): verify live profile/route inheritance
and approval transport for the direct embed, resolve the documented-import linter
mismatch, test the gateway driver if necessary, and audit OTP persistence.
