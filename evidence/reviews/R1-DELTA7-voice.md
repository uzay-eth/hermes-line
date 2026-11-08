# R1 delta 7 — voice path

Reviewed local main **0243257fcbe357d0cc46f81cd2bc21e93c0d1d67**. Scope: L5 voice code, engine voice port and lifecycle wiring, media/digits ingress, for an outbound Grok call to the owner's Spectrum with OTP off.

**Verdict: NO for the first credentialled Grok trial yet.** Fix N9 cancellation cleanup and N10 transport-header logging before using the real xAI credential. No Critical/High finding; both findings are Medium. The normal side-effect gates and media replay checks passed the focused tests. A three-minute phone limit does not clean up a provider connection that was never registered with the call.

This is read-only source review plus disposable offline fixtures. The exact main commit was exported under this directory; the checkout stayed unchanged. No live configuration, keys, provider, phone, real model or `~/.hermes-line/` data was accessed. Tests used the previously established sandbox-exec profile: external network denied, authorized loopback only, explicit environment with scratch HOME/HERMES_HOME/TMPDIR, scratch-only writes and resource/time limits. The reviewed local `websockets` source was the installed dependency, not fetched documentation. No actual spend or xAI billing semantics were tested.

All source references below are relative to the reviewed Hermes Line commit unless explicitly identified as installed dependency code.

## N9 — Medium: cancellation during Grok setup leaves an untracked connection

**Evidence:** `plugin/line/voice/grok.py:82–105`; `voice/controller.py:531–550,831–842`.

Grok setup assigns the acquired socket to the provider at `grok.py:90`, then sends `session.update` and waits for readiness. The cleanup around readiness catches `Exception`, which does not catch `asyncio.CancelledError`. The initial `session.update` send is also outside that cleanup block. Meanwhile, the controller records `self.provider` only after `provider.connect` returns successfully. A canceled setup can therefore leave a connected socket on a provider that the controller never adopted. `VoiceCall.close()` cannot close it because its provider field is still None.

**Concrete failure:** cancel an opening voice coroutine after the WebSocket handshake but while waiting for `session.updated`—for example during task/server cancellation. Call cleanup runs, but the provider socket is not explicitly closed. Its lifetime is now dependent on transport/provider cleanup rather than the call's three-minute limit. This is not a demonstrated normal owner-hangup leak: normal state-driven close waits on the controller lock, and ordinary readiness timeout explicitly closes. The confirmed hole is cancellation during partial initialization; the unguarded initial send is an adjacent error-cleanup gap.

**Reproduction:** `fixtures/probe_voice.py` uses the real `GrokRealtimeProvider` and `VoiceCall._open`, an inert socket whose handshake succeeds and whose readiness receive waits, then cancels setup and calls `VoiceCall.close`. Output:

```text
cancelled_setup_socket_closed_after_call_close False controller_has_provider False
```

The probe explicitly closes the fixture socket afterward. It proves lost cleanup ownership, not measured real xAI charges or how long xAI would retain that session.

**Smallest fix:** make the provider's entire post-handshake setup exception- and cancellation-safe: close the acquired socket on any unsuccessful exit, including cancellation, then re-raise. Have controller `_open` retain cleanup ownership until successful adoption and close a partially initialized provider on failure. Add cancellation-after-handshake and initial-send-failure tests; assert the transport closes even after controller shutdown.

Existing tests cover missing keys, rejected readiness, established-session drops and ordinary teardown, not cancellation during readiness before adoption.

## N10 — Medium: WebSocket debug logging can expose the API key

**Evidence:** `plugin/line/voice/provider.py:151–155` passes Authorization headers to the default `websockets` client without a private redacting logger. Both `grok.py:90` and `stt.py:88` use it. Installed dependency: `/Users/mac/.hermes/hermes-agent/venv/lib/python3.11/site-packages/websockets/client.py:283–294` logs every outgoing handshake header at DEBUG.

**Concrete failure:** enable WebSocket/root DEBUG logging while diagnosing the first voice call. The outgoing Authorization header, including the xAI bearer credential, is logged. Logs copied into a diagnostic bundle or evidence file then disclose that credential. The plugin's own event masking and exception sanitization do not intercept this dependency logging.

**Reproduction:** the second half of `probe_voice.py` constructs the actual installed WebSocket protocol, uses a synthetic Authorization header and an in-memory DEBUG handler, and serializes a handshake without connecting anywhere. Output is only a Boolean:

```text
websocket_debug_contains_authorization True
```

No real key was read or printed. This does not establish that DEBUG is enabled in the live profile; live logging settings were deliberately not inspected.

**Smallest fix:** supply a dedicated transport logger that cannot emit credentials or payload contents, or suppress protocol wire logging for these connections independently of root logging. Test with DEBUG enabled and a synthetic key, asserting it is absent from captured logs for both realtime and STT. Do not rely on a numeric transcript masker to redact an arbitrary API key.

## 1. Secret handling

`engine/hermes_glue.py:98–108` obtains credentials through `agent.secret_scope.get_secret`, using sanitized errors if scope lookup fails. `service.py:535–543` injects that getter into the realtime provider and STT adapter; the key is not embedded in the call briefing or tool specifications.

`grok.py:82–97` reads `XAI_API_KEY` at connection time and passes it in the Authorization handshake header to `wss://api.x.ai/v1/realtime`; the model name is URL-encoded separately. `stt.py:80–92` uses the same credential for `wss://api.x.ai/v1/stt`. Production wiring does not accept a callee-supplied provider URL. Test injection of a different URL/connector is a Python construction capability, not a voice tool.

The Grok local variable is cleared after the handshake; STT's local reference ends when start returns. This is **not secure erasure**, nor proof the key is held only during the handshake: the WebSocket connection/request object can retain the header. The credential necessarily exists in process/transport memory. N10 is a concrete log exposure path.

No application path found puts the key into a model message, transcript, event, tool result or Twilio frame/TwiML. Provider exception messages are generally reduced to types or fixed errors; controller provider-error events record the error code, not the server's free-form message. Tests/evidence use synthetic credentials. There is no ordinary production evidence-file writer in the voice controller.

If the key is supplied through the parent process environment, `engine/acp_driver.py:63–69` copies that environment while removing launch flags, not every credential. Thus “cannot reach a Hermes subprocess environment” would be too strong. This is distinct from sending it into a Hermes conversation/model; the restricted call profile prevents generic file/shell tools. The reviewed code does not deliberately forward the xAI key in ACP prompts. Least-privilege child-environment filtering would further reduce local exposure.

## 2. Spend and connection lifetime

Normal lifecycle checks are substantive:

- `voice/gate.py:26–70` closes realtime voice during IVR/hold except pending fixed speech or explicitly enabled prewarm. Owner-connected and terminal states close it. Defaults disable prewarm (`settings.py:41`). Fixed-line rendering has a speech watchdog (`controller.py:354–361`) and a phrase cache.
- **STT intentionally remains connected during IVR and hold.** `controller.py:587–610` starts it when a stream exists, the call is not closed and Grok conversation is not active. Therefore “zero voice seconds on hold” in tests means zero realtime-Grok connection time, not zero xAI usage or zero STT cost. The meter is not an invoice or total-spend meter.
- Stream end and handoff close tracked STT/realtime connections (`controller.py:226–235,269–278`); terminal state schedules `close` (`:280–284`), which closes tracked connections and the media bridge (`:831–842`). The real connector configures open/close and ping timeouts (`provider.py:154–155`). Closure is asynchronous and may have a short tail; it is not an exact billing cutoff at 180 seconds.
- `engine/machine.py:1074–1093` enforces maximum call duration via a watchdog and terminal-state propagation. The provider call also receives `max_call_minutes * 60` as its time limit. For this trial, the effective engine configuration must actually be three minutes; this review did not read live configuration. Neither provider expiry nor the watchdog can recover N9's untracked socket.
- `controller.py:788–818` retries a dropped provider with exponential backoff, up to `max_reconnects` per drop (default two; validated at most five). There is no endless failure-retry loop for one drop. Successful reconnection followed by another drop starts a fresh allowance, so there is no lifetime reconnect-count budget. The call-duration/terminal checks bound normal trial lifetime, not a global number of xAI requests. Twilio stream reconnection also retains the L1 reconnect budget. STT has no automatic unbounded reconnect loop.

Ordinary hangup, hold transitions, stream recovery and provider-drop failure tests passed. The tests do not prove server billing stops when the client closes, or cover N9's cancellation path.

## 3. Grok tools and callee prompt injection

`voice/tools.py:143–183` treats tool arguments as untrusted, rejects unknown tools and inappropriate phases, bounds rate and concurrent execution per tool, and masks results. Individual handlers validate argument names/types. Tool-call IDs are deduplicated per provider session (`grok.py:224–229`). That is not permanent exactly-once delivery across reconnects, so the engine's own consent/state gates remain necessary.

| Grok tool | Actual boundary |
| --- | --- |
| `ask_hermes` | `engine/voice_port.py:49–63`: bounded question marked as relayed untrusted caller text, sent to the call's existing restricted Hermes driver. It does not create a general-purpose unrestricted agent. Results are masked. |
| `bring_owner` | `voice_port.py:65–67` invokes `CallSession.ring_owner`; configured owner destination, L2 destination policy and the existing single-use owner grant still apply. No model-supplied number is accepted. |
| `press_keys` | `voice_port.py:69–70` invokes `press_digits`; engine menu-state, character/length and per-prompt guards remain. In-band tones and nonce-based fallback target the existing business leg, not a new number. |
| `report_outcome` | `voice_port.py:72–73` invokes `request_finish` with bounded validated outcome/summary. Reporting success is model-controlled information, not owner approval. |
| `end_call` | Router sets an end request; controller waits for response/audio drain with a deadline (`controller.py:711–713,780–785`), then port hangs up the current call. No new dial. |
| `send_verification_code` | `voice_port.py:79–93` uses the existing sealed delivery, task/call binding, owner token and approval generation. With `sms_source: none`, no delivery is installed and the tool refuses. I did not certify live OTP use in this review. |
| `wait_on_hold` | `controller.py:759–765` invokes the engine's hold-resumed transition. Grok closes and STT resumes; it cannot approve or create a call. |

The controller's voice rules explicitly mark callee speech untrusted (`controller.py:47–59`), and conversation context/transcripts are passed as user context (`grok.py:131–135`, `controller.py:559–566`). A spoken injection can still influence what Grok says, requests or reports; prompt wording is not a security boundary. The narrow tool set, restricted Hermes profile and engine gates are the relevant controls. The tool descriptions' references to calendar/memory do not grant those capabilities to the restricted Hermes session. I found no new transfer/arbitrary-number dialing tool, approval tool or browser-fill path.

L5's fake-port tests alone would not establish gate parity. The real-engine rehearsals additionally test blocked owner destinations, single-use owner consent, pre-human tool refusal and sealed-code approval behavior. Those all passed.

## 4. Media and digits ingress

`server/ingress.py:259–293` requires the path secret, signature where configured, a matching call/voice session and fallback REST verification before claiming the media token. `service.py:511–518` requires the current `media-gN` kind; `claim_stream_token` at `:346` checks live session and consumes the bound token. Relay and media kinds are separate, tokens are single-use, and attachment permits only one active stream. The stream's start frame must match the expected CallSid and audio format (`voice/mediastreams.py:115–138`). CallSid correlation is not proof of speaker identity.

`server/ingress.py:249–257` authenticates the digits route as the existing business leg. `service.py:490–509` consumes the one-use digits plan and rechecks menu eligibility; valid delayed plans cannot play after owner ringing/takeover or termination. Only successful eligible digits consumption creates the next media generation. Invalid/replayed plans preserve existing TwiML rather than replaying digits. A token in a returned TwiML response remains a bearer capability.

On call end, engine terminal-state checks prevent new attachment; controller closes the tracked media bridge and provider/STT connections. The single-use/current-generation guards do not protect a leaked *unused* current token from winning first attachment. Fallback still relies on secrecy of bearer URLs; REST checks authenticate record facts, not arbitrary webhook Digits or stream audio. These are the earlier documented fallback limits, not newly fixed by Grok transport.

## Tests run and limits

Commands ran inside the exported target with `/Users/mac/.hermes/hermes-agent/venv/bin/python` and the offline sandbox. Logs are retained beside this report.

| Command | Result |
| --- | --- |
| `python -m unittest discover -s tests/l5 -v` | **71 passed**, 18.404 s (`l5.log`) |
| `python -m unittest discover -s tests/l1 -p test_r5_voice.py -v` | **10 passed**, 6.656 s (`l1voice.log`) |
| `python fixtures/probe_voice.py` (absolute scratch path) | Confirms N9 partial-setup cancellation and N10 DEBUG header disclosure using inert/synthetic data (`probe_voice.log`) |

The L1 suite covers bad secrets/tokens/kinds, generation binding, single use, relay/media separation, real ingress flow, digits replay and delayed digits after owner involvement. L5 covers normal connection/event shapes, key absence/refusal, tool validation, hold/teardown, drop recovery, audio handling and real-engine rehearsals. No first-live xAI/Twilio compatibility, billing or model-injection resistance was experimentally established. No entire L1/L2/L4 rerun was needed for this scoped review.

Coordinator: fix the two small transport-boundary gaps, add the missing failure/debug regressions, then rerun them before the real-key trial. No owner decision is needed for those fixes.
