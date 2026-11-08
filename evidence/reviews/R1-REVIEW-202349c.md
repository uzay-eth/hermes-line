# R1 — call safety, ingress, consent, OTP and Desktop review

**Verdict: NO — not ready for first real calls at this revision.** Fix H2 and M2 before enabling public fallback ingress; fix H1 and M1 before enabling OTP (or disable OTP explicitly for a call-only rehearsal). Resolve the host-authentication validation gap and obtain a passing isolated L1 run before treating the call gates as integration-verified. Browser OTP delivery also has the unresolved destination and disclosure limits described below.

Reviewed revision: `202349cd4fbaff192a6dec2df1af4ceb87661ee0`. Worktree HEAD was `935ef62a43454bff68600919398744bc80eb4e74`; `git diff 202349c HEAD` changes only STATUS.md and docs/briefs/R1-security-review.md. Scoped source, tests and requested context are identical. Worktree initially clean. Read docs/BRIEF.md, CONTRACT.md, ADRs 0002/0003, and L1/L2/L4 lane context. Source evidence below refers to the reviewed revision, with paths relative to the repository.

This is a scoped, single-reviewer source review, with bounded offline test attempts and two in-memory reproductions. No network, provider, phone, keys, or ~/.hermes-line access; no source edits, git writes or commits. Only this report and its containing directory were created. The security-audit skill informed boundary/evidence analysis; its additional artifact/delegation workflow was not used because the task authorizes only this report and does not authorize delegation. No claim of exhaustive whole-product or deployed-host validation is made.

## Findings

### H1 — High: a retained OTP loses its original approval and destination binding

**Evidence:** `plugin/line/sms/delivery.py:194-199,244-257,296-311,325-346`; `plugin/line/engine/service.py:649-671`.

A sealed slot holds only secret, expiration, attempt count and sender. It is indexed solely by task ID. Delivery checks the destination of the *current* registered task, then `_slot_or_release` returns an existing slot without checking its original binding or consuming consent. The production approval path deliberately calls `registry.forget(task_id)` and registers replacement terms, but never invalidates the delivery slot.

**Concrete failure:** approve sender A for origin A; the code is released but typing fails, retaining a slot. Within 120 seconds, the owner approves sender B/origin B for the same task. A retry passes B's origin check and types A's retained code into B, without consuming B's token or matching B's sender/window. The same binding loss can cross fill/call destinations. An injected model can induce a failed/invalid field attempt and later retry; no ability to read the code is needed. Replacement approval does not authorize moving the previous approval's code.

**Observed offline:** using the existing L4 `Base` fixture and `RecordingSink(fail_first=1)`, first fill returned `sink_failed`; replacing the registry entry with sender OTHER and `https://other.example`, switching the origin probe to that origin, and calling fill with **no token** returned `filled`. Boolean comparison confirmed the same retained secret reached the new target. No code value was printed.

**Smallest fix:** bind each slot to an immutable approval generation plus full sender/window/destination binding. Invalidate it on replacement/revocation; reject a mismatch before either sink. Serialize validation and delivery against registry replacement. Add fill→fill, fill→call and call→call replacement tests, including concurrent replacement. Existing retry tests do not cover replacement approval.

### H2 — High: fallback inbound admission permits cached caller substitution and historical-call replay

**Evidence:** `plugin/line/server/ingress_auth.py:91-112,118-121`; `plugin/line/server/ingress.py:235-245`; `plugin/line/engine/service.py:395-402,695-729`; `plugin/line/engine/machine.py:279-297`.

The REST verification cache is a set of SIDs. A cache hit returns success before checking the expected direction, To or From. Inbound expectations contain request-supplied From. Inbound admission neither requires a currently active provider call nor rejects a previously admitted SID. Finishing a session does not call `verifier.forget` or persist an ingress replay rejection.

**Concrete exploit:** an actor who obtains the fallback path secret and a previously verified inbound SID waits until the line is idle, then posts that SID with a different From matching an open owner-mode task. The cache accepts the changed caller without a REST check. `on_inbound` creates a new session, matches the forged caller to the task and returns a fresh relay token to the HTTP requester. The requester can attach and send setup with that known SID; the callback path can spend the task's still-live callback grant to ring the owner. Even without a warm cache, an old completed inbound record with unchanged numbers is accepted because status is not checked. This is not an arbitrary-destination dial bypass: owner ringing still goes to the configured, gated number. It is impersonation of the approved callback and unauthorized use of its task/session authority.

**Observed offline:** a fake REST record for inbound callerA passed verification; verification of the same SID with callerB immediately returned `(True, 'cached')`. The fake record remained callerA. The full phone/HTTP sequence was source-traced, not executed.

**Smallest fix:** key cached identity verification by account plus the complete immutable leg expectation, and reject conflicting expectations. For inbound admission, require appropriate current provider status and persist a unique SID admission record. Legitimate Twilio retries must return the existing call's result without creating a new session or new authority. Expire/evict cache entries. Verify the provider-returned SID too. Add changed-From-after-cache, completed-SID replay, replay after session termination and concurrent admission tests.

**Precondition:** path-secret exposure, not unauthenticated guessing. L1 explicitly acknowledges that the secret travels in provider/proxy URLs. The finding is that the claimed secondary REST identity/replay protection fails after that exposure.

### M1 — Medium: OTP confirmation can approve a different call from the one displayed

**Evidence:** `plugin/line/engine/service.py:616-647,649-665`; `plugin/line/engine/hermes_glue.py:409-414,458-476`; `tests/l1/test_otp_and_plugin.py:100-109`.

`otp_preview` resolves a concrete Destination.call and displays its call ID/counterpart. Its queued/pending payload retains only the string `"call"`, not that resolved destination. `_allow_code` resolves it again at confirmation/execution time.

**Concrete failure:** call A ends while the approval prompt is pending; a callback B attaches to the same task before confirmation. The user confirms the prompt for A, but the grant binds B. Call IDs are expressly part of the approval contract even where the displayed phone number is unchanged. An impersonating callback compounds the consequence; no impersonation is required to demonstrate the binding change.

**Smallest fix:** carry the exact resolved destination in the server-owned pending approval; compare its call ID/counterpart and liveness at execution. If it changed, refuse and require a new prompt. The existing test checks prompt equality only while the call remains unchanged.

### M2 — Medium: replayed Connect action consumes recovery budget and hangs up a live call, including in signature mode

**Evidence:** `plugin/line/server/ingress.py:91-115,168-174`; `plugin/line/engine/machine.py:712-725`; `plugin/line/server/twilio_signature.py:19-22,37-44`; `plugin/line/engine/config.py:36`.

The Connect action increments reconnects for every accepted POST. After the default two attempts, another accepted POST schedules a hangup. There is no delivery/recovery generation or idempotency check. HMAC validates content, not freshness.

**Concrete failure/exploit:** duplicate delivery of a legitimate Connect callback, or an observer replaying the same captured signed URL/body/header, consumes multiple recovery attempts and terminates a live call. Fallback requires only the exposed secret and correlated SID. The defect can be triggered without forging a signature or changing signed content.

**Smallest fix:** process each Connect completion/recovery generation once; cache and return its TwiML for retransmissions. Do not debit the retry budget until a new recovery transition actually occurs. Bound token issuance by that generation. Test duplicate callbacks in both authentication modes. Do not describe signatures as replay protection.

### M3 — Medium: Desktop revision protection ends at queue insertion

**Evidence:** `plugin/line/store/db.py:328-357`; `plugin/line/engine/service.py:463-510`; `plugin/line/server/desktop_api.py:137-139`.

The requested revision is checked while inserting the command but is not stored. Execution may occur later, after webhook-driven state changes. `_execute` cannot compare the owner's observed revision with the current state.

**Concrete failure:** an owner queues Say while observing IVR/on-hold; before execution the session becomes agent_talking with a human. Say remains a valid operation in the new state and the stale instruction is spoken to a different audience. Similarly, a queued takeover can run after its observed call state changes. This is a stale-control correctness boundary, not a remote authentication bypass.

**Smallest fix:** persist expected control revision and recheck immediately before action execution; return a stale result if it changed. Keep the existing idempotency transaction. Add a test pausing command consumption, changing call state, then resuming it. Current tests only make the revision stale *before enqueue*.

## Answers to the requested checks

### Call creation and destination gates

There are two production call-creation callers: outbound `_dial` (`service.py:550-575`) and owner `ring_owner` (`machine.py:541-567`). Both run `DialGate.authorize`, including destination and single-use consent, immediately before scheduling the adapter work. `telephony.py:63-75,90-99,167-175` carries the canonical destination into a once-used permit and then the provider's To field. The adapter does not re-run policy itself; permits are mutable Python objects, so this is an internal coding boundary, not protection against arbitrary Python execution. No lower-trust input mutation path to a permit was found.

Conference takeover redirects the *existing* business SID to a generated conference URL (`machine.py:581-607`); generated conference TwiML joins existing legs. No third-party-number transfer, participant-create API, automatic business redial, or independent verification-call creator was found in scope. Relay retries create sockets, not PSTN legs. Callback handling admits an inbound provider leg and can call the same owner-ring gate. A future creator must not bypass that gate.

The literal claim “no path accepts a number from the model” is too broad: `hermes_glue.py:215-234` intentionally accepts a model-proposed destination to create a pending task. It does not dial without a separate owner action. Speech/DTMF/webhook parameters do not feed an outbound To field. Webhook From affects callback/task identity, with H2's exception to its intended validation.

`policy/destinations.py:35-60` rejects noncanonical ASCII, URI/prefix/punctuation/unicode encodings, short codes, Bora and non-US/unsupported NANP area codes before checking the allowlist. Even a hand-built object cannot disable these hard checks. Settings validate immutable mandatory approval/allowlist defaults (`settings.py:19-42`). No encoding bypass was found. This is not a proof of subscriber ownership, routing/forwarding endpoint, or all full-length emergency-service numbers; the trusted exact owner allowlist remains necessary. L2 documents this limitation.

### Ingress, stream tokens and fallback completeness

Signature mode requires the path secret, account and HMAC over configured public URL/body (`ingress.py:59-115`), and per-leg correlation (`124-144`). A missing token after signature-mode startup fails closed. Mode is chosen at startup (`service.py:266-279`); a secret-fetch error is indistinguishable from intentional absence and selects fallback. That is an operational downgrade risk to surface explicitly, not a demonstrated remote trigger.

Stream tokens are random, persisted as hashes, transactionally single-use and bound to call/kind (`service.py:299-310`; `store/db.py:313-325`). The relay requires path secret and, in signature mode, a signed upgrade (`ingress.py:250-271`). REST validation happens on HTTP TwiML issuance, not independently on the socket; fallback security therefore depends on who can obtain fresh TwiML. Setup checks CallSid, but frames can be submitted before setup (`machine.py:261-285`), so setup is not an additional authentication credential. One-socket exclusion is useful but cannot distinguish an attacker who gets there first. Body/frame bounds and disabled uvicorn access logs are present (`ingress.py:97-100,274-276`; `runner.py:43-45`).

**L1's fallback limitation list is not complete.** It correctly discloses secret exposure, forged live-call events, forged owner press-1 once REST says in-progress, relay-token minting via Connect, and REST outage/rate-limit dependence (`docs/lanes/L1.md:296-314`). Add H2: historical inbound admission, SID-only cache bypass of expected caller, no cache eviction, and replay across sessions/restarts. Also disclose that `/answer` can mint fresh relay tokens repeatedly for a live outbound session (`ingress.py:146-153`), not only Connect. Correct the implication that signatures prevent replay (M2). Per-call tokens are not a remedy when an accepted HTTP request gives an attacker new ones. REST shows a leg's existence/status, not the authenticity of the event or physical caller identity.

### Consent and process boundaries

The primitive is sound against the tested misuse: action/task/full JSON binding hash, random token, bounded TTL, monotonic expiry, lock, pop-before-compare, mismatch burns token, restart revokes the in-memory entries (`policy/consent.py:10-50`). L2 tests exercise concurrent consumption, expiry, cross-task/action/binding mismatch and restart. `GrantBook` renewal consumes the previous bridge grant and reissues identical terms within a lifetime (`service.py:110-132`); it is an intentional renewal, not a fresh approval per hour.

Owner operations are queued to the owning service process; consent is issued and consumed there (`service.py:434-548`). Tools submit source `tool`, not caller-selected source (`hermes_glue.py:251-257`), and approve/deny/allow_code require desktop/slash (`service.py:472-484`). Phone input cannot directly mint those grants. Private owner-leg press-1 accepts an already-authorized bridge; it does not authorize a new destination. Fallback can forge that press, as documented.

Slash dispatch ignores supplied platform/source identity and trusts the host to invoke the command only for an authorized owner (`hermes_glue.py:289-294`). That host guarantee is not established by scoped tests. Generic model/tool approval modes and profile tools are outside this plugin's authority; a prompt saying speech is untrusted is not a sandbox for arbitrary host tools.

### OTP and masking

The registered tools return status metadata; tokens are not model arguments (`sms/hooks.py:95-130`). Release verifies registered terms, sender/window/matching, consumes consent, persists the released-message key, then registers the exact value with the scrubber (`sms/service.py:167-257`). `ReleasedOtps` stores hashes in SQLite and fails closed when unavailable (`store/released_otps.py:20-46,65-74`). Sealed call delivery checks the live counterpart and records only a marker (`engine/sealed.py:33-44`; `service.py:680-692`; `machine.py:503-524`). This supports the intended direct-delivery secrecy, subject to H1/M1.

The stronger statement “code never in any tool result/transcript/log/Desktop/persisted store” is **not established**. Exact scrubbing is process-local, limited to 32 values/one hour (`sms/scrub.py:35-58,86-95`). Tool-result hooks handle strings only and host dispatch can skip hooks (`sms/hooks.py:135-158`; L4 residual-risk section). A later browser snapshot after expiry/restart is not protected by this exact-value hook. Speech masking deliberately leaves uncued, previously unknown bare codes readable; engine transcript persistence invokes that masking (`machine.py:300-306`; `store/db.py:275-282`). L4 documents multimodal/reasoning/concurrent-observer leak paths. These are limits, not evidence of an actual production leak in this offline review.

Browser fill checks top-level origin **before** waiting for SMS and then dispatches typing separately (`sms/delivery.py:249-278`). Navigation during the wait and cross-origin frame targets are not atomically bound to the approved origin. The downstream browser implementation's behavior on stale refs/frame targets was not validated here; this remains a release blocker for a strict destination guarantee. Smallest safe interim treatment is to disable browser OTP delivery until typing can enforce the approved document/frame origin atomically. Re-probing immediately before typing reduces but does not close the race.

### Desktop REST

Public telephony ingress has no Desktop control routes (`ingress.py:289-302`). Desktop command actions/payloads are constrained, idempotency is transactional, and stale-at-enqueue revisions are rejected. There is no local authentication dependency in `create_router` (`desktop_api.py:163-198`); it deliberately relies on the Hermes namespace mount for owner authentication. `dashboard/plugin_api.py:21-23` only exports that router. The test mounts it on a bare FastAPI app and successfully accesses it without credentials (`tests/l1/test_ingress_desktop_lifecycle.py:221-235`). That tests routing, **not owner authentication**, and is not proof of a deployed auth bypass. Host mount/middleware, cross-profile access, remote OAuth scopes and CSRF behavior need validation against the actual Hermes deployment before certifying owner-only controls. M3 covers the revision gap.

## Tests run and what they establish

Lane commands from documentation:

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/mac/.hermes/hermes-agent/venv/bin/python -m unittest discover -s tests/l1 -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=plugin python3 -m unittest discover -s tests/l2 -v
PYTHON=/Users/mac/.hermes/hermes-agent/venv/bin/python tests/l4/run_tests.sh -v
```

All attempts used the installed Hermes Python 3.11 interpreter, including L2, in `/usr/bin/sandbox-exec`, from an explicit environment allowlist. Profile denied network and all filesystem writes, denied reads under /Users/mac except this repository, the Hermes code/venv and its Python runtime, and explicitly denied ~/.hermes-line. Bytecode was disabled. Temporary directory was the approved report directory, still read-only to test processes. Limits: 40 seconds CPU, 55 seconds subprocess wall time, 0-byte file-size limit, 256 descriptors; initial NPROC 64, later 1024. macOS rejected attempts to set RSS/AS limits; no enforceable memory cap is claimed. No dependencies installed. No test logs were written.

| Suite | Observed result | Interpretation |
|---|---|---|
| L1 | Exit 1; 122 tests, 103 errors, 0.541 s | 19 completed; extensive fixture/setup errors, including “No usable temporary directory.” No passing call-flow/auth/SQLite integration claim. |
| L2 | Exit 0; 48 tests, OK, 0.049 s | Destination/settings/consent and deterministic simulation checks pass under these limits. |
| L4 script | Initial exit 128 (fork blocked by NPROC 64); later exit 1 (sandboxed shell cd failure) | Runner failures, not product assertions. |
| L4 equivalent discovery | Exit 1; 241 tests, one setUpClass error, 1.965 s | 241 tests completed successfully; RealCore's two tests never ran because its temporary home could not be created. |

The successful L4 discovery invocation was:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=plugin \
  /Users/mac/.hermes/hermes-agent/venv/bin/python \
  -m unittest discover -s tests/l4 -t tests -v
```

Two intermediate runner attempts failed before launching target code because macOS rejected the requested memory resource limit. These are not suite results. The subsequent in-memory H1/H2 reproduction exited 0 and printed only statuses/booleans:

```text
slot_rebind sink_failed filled old_secret_sent_to_new_target True
rest_cache (True, 'verified') (True, 'cached')
```

Reproduction construction: import the existing `l4.test_delivery.Base`, run setUp, use its `delivery(RecordingSink(fail_first=1))`, fill t1 at @e7; forget/re-register t1 with sender OTHER and Destination.fill("https://other.example"), switch its origin probe, fill t1 at @e8 with token None. For H2 construct RestVerifier with a fixed dummy inbound record and verify two LegExpectations sharing SID/To but differing in From. No provider calls or persistent fixtures are involved.

**Test adequacy:** L2's destination matrix includes 57 malformed/denied shapes and hand-built settings (`test_policy.py:18-45`), but its seven named “paths” are just labels around the same policy function (`57-65`), not actual call-path integrations. L1's gate/ingress tests are meaningful fake-adapter integration tests when permitted to run. They test wrong numbers/accounts, unknown SID, wrong signature, token reuse, pre-answer forged owner accept and stale-before-enqueue controls. They do not test warm-cache caller substitution, historical inbound replay, duplicate Connect debit, approval target changing between prompt/confirmation, or stale-after-enqueue execution. L4 strongly covers sender/window/single-use/status-only results and ordinary retries, but assumes immutable approval terms during retry and misses H1. Its real-core tests mock the browser and call sinks and would not establish actual browser navigation/frame-origin behavior even if their setup passed.

## Coordinator follow-up and release gates

1. Fix H2/M2 and add regression tests before public fallback ingress. Fix H1/M1 before OTP, or explicitly disable SMS tooling for the initial call-only exercise. Fix M3 before claiming Desktop revision safety.
2. Re-run L1/L4 in a disposable offline sandbox allowed to write temporary fixture databases and bind isolated loopback. The strict “write only REVIEW.md” constraint prevented that evidence here; the observed setup failures are not product failures. Do not substitute historical lane test counts for this run.
3. Verify the actual Hermes Desktop/slash owner boundary and profile isolation. Prove rejected unauthenticated/remote requests create no command rows. Validate browser document/frame binding and post-fill redaction, or leave browser OTP disabled.
4. This pin provides ConversationRelay ingress; it does not contain the promised Grok Media Streams integration. No conclusion about future L5/media ingress follows from this review.

Questions for the coordinator to route to Can (no owner decision is inferred):

- For the first call-only trial, options are (A) signature mode once the Auth Token is available, (B) corrected fallback with its explicitly disclosed bearer-secret limitations. Recommendation: A; if unavailable, B only after H2/M2 regression coverage and explicit acceptance of the remaining live-event forgery risk.
- For OTP during that trial, options are (A) disable OTP pending binding/browser fixes, (B) include OTP after fixing and validating the relevant paths. Recommendation: A for the initial telephony-only trial.
- For completing offline verification, options are (A) authorize disposable temporary fixture files and isolated loopback while keeping the checkout read-only, (B) retain report-only writes and accept incomplete integration validation. Recommendation: A before any readiness sign-off.

No Critical finding was established. H1 and H2 are the High findings. No real-call authorization or TWILIO-GO is implied by this report.
