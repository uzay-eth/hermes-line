# R1 delta 3 — 1d16bfd

**Verdict: YES for the specified call-only ConversationRelay trial to the owner's own two phones, with `sms_source: none`, browser fill off and fallback Twilio ingress without the Auth Token, now using the session MCP endpoint.** N2 and the N1 residual are fixed. No Critical, High or Medium finding was established in this delta. One Low lifecycle issue remains: token revocation/rotation does not invalidate an already authenticated request waiting for its body, unless the call also ends. It does not bypass destination/owner-consent gates or allow access to another call.

Reviewed `2976a70..1d16bfd495a0481df14b8cb2b060ffaba75b5206`, concentrating on L1 `6a02b36`, L4 `890a4f1`, L1 `c5f721e`, the new MCP endpoint and L1 sections 13–14. Plugin references below are **at 1d16bfd**, read/tested from `git archive` under `disposable/`; the original checkout remains clean and unchanged. The gateway references are local source under `/Users/mac/.hermes/hermes-agent/`, whose HEAD was `bf0203a794a07aac3ff764972ab9087baa4e4f4e`. No live gateway, external network, Twilio, phone, real credential or `~/.hermes-line/` was accessed.

## Prior findings and reproduction results

### N2 — fixed

`plugin/line/engine/machine.py:554-568` introduces the shared menu gate: eligible state, not terminal, no handoff, no end request and no owner leg. `engine/service.py:416-443` consumes the nonce, then checks that gate before producing any digits or changing the stream generation. Refusal returns the preserving document. `engine/voice_port.py:96-103` also checks before submitting a redirect.

I reran the unchanged prior reproduction. It failed its old assertion that `<Play digits="2">` would be returned, because the response is now the conference. After changing only those defect-expecting assertions, the same delayed-webhook sequence produced in **both signature and fallback modes**:

```text
status 200 state owner_connected plays_stored_2 False plays_injected_9 False
stream False conference True generation_delta 0
```

Replay still returns the conference without digits. The reproduction creates a nonce during hold, has the owner accept takeover, waits for the media stream to close, and only then submits the previously valid digits webhook. Provider actions are fake; HTTP/WebSocket transport is real loopback.

The new `test_N2_delayed_digits_webhook_after_owner_takeover_changes_nothing` (`tests/l1/test_r5_voice.py:255-308`) genuinely exercises this ordering in both auth modes and checks no Play/Stream, unchanged generation/documents, consumed nonce, preserved conference and refusal of subsequent menu/port requests. `test_N2_digits_refused_while_the_owner_is_ringing` (`:310-336`) adds the ringing state. These tests substantiate the fix. This does not claim pending plans are eagerly purged inside L5; the final route/port gate is sufficient for the reported stale request.

### N1 residual — fixed

`plugin/line/sms/delivery.py:195-218` now refuses missing/invalid generations and sinks that cannot accept them. `:366-369` checks compatibility before release/phone fetch. `plugin/line/engine/sealed.py:89-97` no longer adopts the current generation when the caller omits it; it refuses instead. The existing final generation/ticket checks remain in place.

The unchanged prior three-argument-wrapper reproduction can no longer reach its pause barrier: the wrapper is never called. Its old `entered.wait(3)` assertion fails for that reason. Updating the harness to expect early refusal, while retaining the owner replacement and no-late-frame checks, gives:

```text
event_loop_blocked False delivery_result sink_unsupported replacement_used used late_dtmf_frames 0
```

The generation-aware overlap reproduction also still passes: `sink_failed`, responsive loop, and zero late DTMF frames after replacement.

New regression coverage is substantive:

- L1 `tests/l1/test_r1_fixes.py:657-667` directly refuses missing, `None`, boolean and string generations even with a live grant. The legacy-wrapper test at `:669-699` also sends nothing. In the merged tree L4 refuses before the wrapper is entered, so that test's five-second wait no longer establishes an actual paused-sink overlap; the lower-level missing-generation test independently covers L1's guard.
- L4 `tests/l4/test_r1_fixes.py:369-384` proves a legacy sink is not called and the phone is not read; `:386-407` replaces approval at the compatibility check and still invokes no sink. `:409-416` exercises generation validation at the sink boundary.
- The previously misleading slot-preservation test has been corrected: `:418-435` now installs a newer slot during the send and verifies the completed old send does not delete it.

The original lock/late-send fixes remain exercised by the L1/L4 suites and the independent generation-aware reproduction.

## MCP boundary assessment

### Listener, Host and browser requests

`plugin/line/server/runner.py:55-65` opens a separate ephemeral listener, default `127.0.0.1`, distinct from tunnelled Twilio ingress. `engine/call_tools.py:67-74` only permits the literal loopback host choices; the production service uses the numeric default. Allowed Host values include only the loopback names with the actual port. `server/call_tools.py:49-57` checks Host and requires a bearer before reading the body. Proxy headers are disabled; access logs are disabled. The tools app has only `/mcp` (`:90`); no MCP route was added to public ingress.

**Another local process without the bearer cannot drive tools merely because it can connect to loopback.** It can forge a permitted Host, so Host is not process authentication; the 256-bit bearer is the credential. With a stolen bearer, any local process can act on that bearer’s call. There is no OS peer-UID, PID or ACP-session proof. This is a per-call capability, not isolation from code already able to read the service/ACP/gateway process memory or control the same owner gateway session.

**A web page without the bearer cannot drive tools through ordinary DNS rebinding or CSRF to this endpoint.** Rebinding retains the foreign hostname and fails Host validation. Direct requests to `127.0.0.1` have an allowed Host but no ambient bearer: cookies, query parameters and Basic auth are not accepted. A cross-origin fetch setting Authorization needs preflight; the app provides neither an OPTIONS handler nor CORS permission. Simple POSTs, including `text/plain`, still require Authorization. There is no Origin check, but that omission alone does not defeat the non-ambient bearer boundary.

Supplemental real loopback HTTP checks returned **401** for uncredentialed cross-origin simple POST and cookie/query-token attempts, **405 without CORS headers** for preflight, and **421** for a foreign Host even with the correct fixture bearer. No tool state changed. These are browser-shaped HTTP tests, not a full browser automation/DNS laboratory. They do not claim protection against a malicious extension or a compromised same-owner process.

### Tokens, call binding and replay

`engine/call_tools.py:90-115` issues 32 random bytes, keeps SHA-256 digests in its registry, removes previous tokens for that call, checks expiry and supports revocation. `engine/service.py:170-172` gives tokens a lifetime of `max_call_minutes * 60 + 600`; `:516-520` revokes and removes the session at call end. `engine/machine.py:200-223` supplies the spec before driver start and revokes on startup failure.

`server/call_tools.py:54-57,79-85` resolves the call from the bearer and checks that the same live session still exists before dispatch. Tool arguments cannot select a different call; `engine/call_tools.py:59-64` ignores extra `call_id`/number arguments. This is looser than the advertised `additionalProperties: false` schema, but does not permit cross-call routing. Another call's own token authorizes only that other call. The endpoint exposes neither a token lookup nor the token digest registry.

These are reusable call-lifetime tokens, **not single-use tool grants**. JSON-RPC IDs are not deduplicated. Replaying an authorized tool call is possible, subject to the same engine menu/owner/finish behavior as repeated in-process calls. Owner consent is still separately consumed by the existing engine gate. Revocation at actual call end works even for a request whose body was delayed; the supplemental test got 401 and no mutation after end. The narrower live-call revocation issue is below.

### Tool parity and side effects

There are exactly three methods (`engine/call_tools.py:30-52`): press digits, request finish, and request owner. Definitions and argument conversion are shared with in-process registration (`engine/hermes_glue.py:20,71,256-259`); MCP dispatch uses the same `CallSession` methods (`engine/service.py:750-782`).

- **Dial/transfer:** no MCP method creates an arbitrary call, accepts a destination, approves a call or directly takes over. Unknown owner/dial/OTP tools are rejected at `server/call_tools.py:79-82`.
- **Owner ring:** `machine.py:643-647` only sets the same request flag as the existing tool. `_after_turn` reaches `ring_owner` (`:531-539`); `:650-676` selects the configured owner, takes the scoped existing grant, and performs destination/consent authorization before `place_call`. A supplied number cannot replace that destination. The supplemental test removed the existing grant, requested owner through MCP, processed the after-turn path, and observed `owner_ring_rejected` with no additional fake provider leg.
- **DTMF:** MCP invokes `press_digits` and the shared menu/state, digit-format and one-press-per-prompt checks (`machine.py:554-580`). It does not invoke the adapter directly. This preserves existing menu behavior; it is not an extra owner consent for each key.
- **Finish:** the same validated outcome/masked-summary request is recorded (`machine.py:635-641`), and normal after-turn handling ends the call. This is intentionally available to the call agent.
- **OTP:** no OTP delivery, approval or sealed-send method is exposed. `sms_source: none` still returns before OTP setup (`engine/hermes_glue.py:342-348`). The new endpoint grants no OTP authority.

## What the gateway retains about the bearer

The raw token does reach trusted process memory; “hash stored” describes the token registry, not every object in the process.

1. Hermes Line's driver retains the raw server/header spec in `_mcp_servers` and writes it to the ACP stdin pipe in `session/new` (`plugin/line/engine/acp_driver.py:200-219`). It is not put in the briefing or a model tool argument. The auxiliary ask session gets an empty server list (`:319-325`).
2. `acp_adapter/gateway_server.py:281-289` forwards the full header through gateway `session.create` and retains it in the adapter's `_editor_mcp` dictionary.
3. `gateway/session_policy.py:478-486` calls `private_editor_request`. `gateway/session_local_mcp.py:73-81` extracts the full spec into private memory and replaces the public/store-bound request field with a fingerprint of the complete server spec. `gateway/session_policy.py:805-807` then binds that private spec.
4. The frozen policy stores connection scope, fingerprint, selected schemas and names, **not headers/token** (`gateway/session_local_mcp.py:233-257`). The raw spec lives in authority `_local_editor_mcp` memory (`:271-279`) and is converted to HTTP client headers (`:128-141`). `gateway/session_local.py:340-342,442-446` passes the sanitized policy for storage and publishes the sanitized launch request to viewers. Schema cache entries likewise contain fingerprints/schemas, not header values (`tools/mcp_schema_cache.py:75-94`).
5. Session scopes are distinct (`gateway/session_local_mcp.py:98-108`). A gateway-authorized fork can inherit the parent's server specification (`:462-491`; adapter `gateway_server.py:451-460`), hence the **same call bearer**, although the connection scope differs. That child can act on the parent's still-live call. The endpoint does not bind to one ACP session identity beyond bearer possession. Ordinary independently created calls get different tokens. This inheritance requires the gateway's branch authority; it is not token discovery by another arbitrary call.

A sandboxed probe using synthetic data exercised the real private-request, bind and attach helpers, substituting only discovery/transport with a fixture: raw token absent from the request and frozen policy, present in private memory; scopes differed between sessions. No live gateway was launched or state database inspected. Thus the reviewed paths support “no raw token in the durable session policy/request,” not a blanket claim about every host log, crash dump or privileged memory reader.

Gateway source fingerprints (SHA-256): `acp_adapter/gateway_server.py` `576bbee1cb258aacd1f47855360472ad5ff6b9fe684a7573db20bdcd1f83b5bd`; `gateway/session_local_mcp.py` `a938eee2aaf886750b960b5d2c6224becd613e8c96e166fcf2485603d52d81bf`.

## N3 — Low: live-call token revocation does not fence a pending request

**Evidence:** `plugin/line/server/call_tools.py:54-58,83-85`. The bearer is resolved before `await request.body()`. After that await, the code rechecks session identity/liveness, but never resolves the bearer again.

**Concrete failure:** a previously authorized local client sends valid request headers and pauses before sending its JSON body. After the server authenticates those headers, the token is revoked, rotated, or expired while the call remains live. The client then sends a finish-tool body. The tool still records `finish_outcome=done`, although a fresh request with that old bearer is rejected. A subsequent ordinary after-turn can end that same call.

Bounded split-body HTTP reproduction:

```text
pending_request reissue status 200 finish_mutated True
pending_request revoke  status 200 finish_mutated True
pending_request expiry  status 200 finish_mutated True
pending_request end     status 401 finish_mutated False
```

The expiry case advances only the injected token clock; the revocation/rotation cases use the real registry methods. The harness wraps `resolve` only to signal that authentication occurred, then uses a real socket to delay the body. No token values are logged.

**Severity rationale:** prior possession of a valid call capability is required, the call must remain live, and the effect stays within that call's existing tools/gates. The normal end-of-call cutoff works. There is no current owner-facing live-call token rotation flow in the reviewed wiring, so this is not a blocker for the requested trial.

**Smallest fix:** retain the presented bearer and re-resolve it immediately before `svc.call_tool`, requiring the same call ID, along with the existing session check. Add a split-body regression for reissue/revoke/expiry and retain the passing end-of-call case. Existing R6 tests check invalidation before a request or after call end, not while reading its body.

One additional implementation limit: `request.body()` buffers the whole authenticated body before the 64 KiB check (`server/call_tools.py:58-60`). The current size test establishes 413 after receipt, not a hard memory cap. A bounded streaming read and read deadline would make the documented cap accurate. No memory-exhaustion test was attempted; this is not presented as a separately proven availability exploit.

## Tests run and coverage limits

Executed from the immutable `disposable/` export with `/Users/mac/.hermes/hermes-agent/venv/bin/python`. `run_suites.py` records the exact allowlisted environment and sandbox: only loopback network, only fixture-directory writes, scratch HOME/HERMES_HOME/TMPDIR, bytecode disabled, explicit denial of `~/.hermes-line/`, CPU/file-size/process/descriptor limits and wall-clock timeouts. No dependencies were installed. As in prior passes, macOS sandboxing here is not a separate loopback namespace or hard RSS quota.

```sh
python -m unittest discover -s tests/l1 -v
PYTHON=/Users/mac/.hermes/hermes-agent/venv/bin/python HERMES_AGENT_DIR=/Users/mac/.hermes/hermes-agent sh tests/l4/run_tests.sh -v
python ../fixtures/repro_otp_lock.py
python ../fixtures/repro_media_edges.py
python ../fixtures/repro_otp_legacy_fixed.py
python ../fixtures/repro_otp_lock_current.py
python ../fixtures/repro_media_edges_fixed.py
python ../fixtures/repro_mcp_edges.py
python ../fixtures/probe_gateway_storage.py
```

| Verification | Result |
|---|---|
| L1 suite | **184 tests passed**, 64.463 s, exit 0; no skips reported. |
| L4 suite | **275 tests passed**, 4.144 s, exit 0; no skips reported. |
| Unchanged old N1/N2 exploit assertions | Fail because the old effects no longer occur, as explained above; retained as evidence, not counted as current-suite failures. |
| Updated N1 legacy and generation-aware reproductions | Pass; no loop stall or late DTMF. |
| Updated media-edge reproduction | **4 tests passed**, 2.304 s; N2 fixed in both modes; prior pre-start/wrong-SID/expiry/fallback-failure checks still pass. |
| MCP supplemental checks | **3 tests passed**, 1.302 s; browser-shaped refusals, gated owner request, absent dial/OTP tools and N3 lifecycle behavior. The N3 test asserts the observed defect, not a fix. |
| Gateway synthetic storage probe | Pass; only sanitized request/policy material, raw credential in private memory. Discovery/transport mocked; no live gateway. |

R6 regression tests genuinely cover token randomness/binding/reissue/expiry/revoke (`tests/l1/test_r6_call_tools.py:59-86`), tool definitions and behavioral parity (`:126-171`), old-call/cross-service tokens and ignored target override (`:173-193`), invalid auth/Host/method/body responses (`:195-218`), end revocation/listener shutdown (`:220-249`), ACP spec transfer and a separate scripted agent process using MCP to finish its fake call (`:265-302`). The cross-profile test uses a fake ACP agent, not the actual Hermes gateway. The listener test confirms loopback reachability and shutdown; literal binding restrictions are additionally supported by the constructor test and source. These tests did not previously cover browser preflight, delayed-body revocation, gateway persistence or owner-grant denial through MCP; the supplemental checks/source trace address those stated limits.

Artifacts are retained only under this scratch root: `l1.log`, `l4.log`, reproduction `.log` files, `probe_gateway_storage.log`, fixture scripts and `run_suites.py`. No L2/L5/full gateway suite, live model, real call or tunnel was run. No code fix is required by this delta before the narrowly specified trial; N3 should be corrected before relying on token rotation alone to withdraw an active caller's capability. No owner decision is needed.
