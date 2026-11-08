# R1 delta review — dae1b78

**Verdict: NO for the first call-only live trial at this commit, including OTP off, browser fill off and fallback ingress without the Auth Token.** M2 remains reproducible: overlapping Connect callbacks for one generation can exhaust recovery and hang up a call. Fix the concurrency gap before that trial. A separate new OTP locking defect must be fixed before OTP is enabled, but is not a blocker when OTP is off.

Reviewed `202349cd4fbaff192a6dec2df1af4ceb87661ee0..dae1b78474b8d0632430a66cbb9a060dd00d5a4e`, limited to the requested fix paths and their regression tests, with L1/L4 round-4 notes and the revised fallback limitations. All source references below are **at dae1b78**, not the unchanged older worktree. No code changes, git writes, commits, external network, real provider calls, phone access or keys. No ~/.hermes-line access. The checkout remains clean.

## Disposition of R1 findings

| Finding | Disposition | Source evidence and regression assessment |
|---|---|---|
| **H1: retained OTP redirected under replacement approval** | **Fixed for the original binding defect.** New locking regression N1 below. | `plugin/line/sms/service.py:108-187,295-330` gives approvals generations/canonical bindings, invalidates on change, and checks generation at commit. `plugin/line/sms/delivery.py:200-204,235,347-359,378-414` binds slots, checks again before sending, and invalidates on replacement. L4 `test_r1_fixes.py:43-115` really exercises fill→fill, fill→call, call→call, revocation, identical-terms/new-generation replacement, and listener-disabled defense. Tests at `117-171,218-242` exercise replacement during a held browser sink/probe and during phone fetch. They do not exercise the production call sink's dependency on the service event loop (N1). |
| **H2: cached caller substitution / historical inbound replay** | **Fixed.** | `plugin/line/server/ingress_auth.py:101-163` binds cache hits to account/SID/direction/To/From, checks returned SID, expires/bounds the cache, and refreshes status when required. `server/ingress.py:237-247` requires live REST status for fallback inbound admission. `engine/service.py:423-425,792-834` serializes admission, returns the existing result, refuses ended/restarted admissions and evicts verification entries. `store/db.py:414-428` persists unique SID admissions. L1 `test_r1_fixes.py:38-87,94-199` tests changed caller/account, TTL/bounds, provider SID, live status, completed replay, retry identity, end/restart replay and **eight concurrent admissions**. These are meaningful tests of the original defect, including real local HTTP and SQLite paths. |
| **M1: OTP confirmation resolves a different call** | **Fixed.** | `engine/service.py:682-759` persists the displayed concrete destination in a server-owned pending approval; confirmation checks its exact call/counterpart/liveness and consumes the pending row once. `store/db.py:430-466` stores prompt sequence and executed approval generation. `engine/hermes_glue.py:381-382` wires the generation lookup into L4; `sms/hooks.py:96-104,118-122,143-145` forwards it and rejects unknown generations. L1 `test_r1_fixes.py:377-445` actually ends the displayed call, creates its callback, rejects the old confirmation, tests counterpart change, single use and supersession, and tests old-generation rejection against L4. L4 tests at `245-299` check generation refusal before phone fetch, retained-slot generation, call delivery and tool wiring. |
| **M2: Connect replay exhausts retry budget** | **Partly fixed — still blocks the call-only trial.** | `engine/service.py:383-411` caches answer TwiML and binds stream tokens to current relay generation. `engine/machine.py:730-758` caches completion results but checks the cache/current generation **before an await**, without checking again afterward or serializing overlapping requests. L1 `test_r1_fixes.py:221-306` meaningfully tests sequential duplicates, sequential replay after recovery, unknown generations, a live socket, and answer retransmission. The duplicate calls at `231` are a sequential list comprehension. No test overlaps completions during the socket-close wait. See the reproduced failure below. |
| **M3: revision checked only when queued** | **Fixed for the reported stale-after-enqueue path.** | `store/db.py:130-132,344-365` migrates/persists expected_revision; `engine/service.py:509-511,546-557` checks it at execution before dispatch. L1 `test_r1_fixes.py:311-336` pauses command claiming, queues Say, changes the call from hold to human conversation, resumes claiming, and verifies stale rejection and no speech. Its companion at `340-350` verifies unchanged control state remains actionable despite transcript activity. This is a substantive regression test, not merely a repeated enqueue-time check. |
| **Signature-downgrade note: secret read error silently selects fallback** | **Fixed for secret-accessor failures.** | `engine/hermes_glue.py:108-118` propagates accessor errors; `engine/service.py:272-290` stops before serving when retrieval raises, and enforces explicit `ingress_auth: signature` when the token is absent. Config/manifest expose the choice. L1 `test_r1_fixes.py:449-482` verifies service misconfiguration/no server on retrieval failure, explicit signature mode without a token, and glue exception propagation. Scope caveat: glue still treats inability to import the Hermes secret module as absence (`113-114`); these tests cover the accessor, not module-import failure. Auto mode intentionally permits genuinely absent-token fallback, as requested for this trial. |

“Fixed” here resolves the specific R1 finding; it does not certify all host authentication or all OTP behavior.

## Remaining M2 — Medium: concurrent completion replay still hangs up the call

**Evidence:** `plugin/line/engine/machine.py:739-757,768-774`; `plugin/line/server/ingress.py:168-177`.

Several requests for generation 0 enter `connect_completed` while its relay socket is still connected. Each passes the `action_results` and `relay_gen` checks and waits at `await asyncio.sleep(0.1)`. When that socket closes, each resumes and calls `relay_action` without rechecking whether another request already completed generation 0. The first creates generation 1, the next can create generation 2, and another exhausts the budget and schedules a provider hangup. Responses differ, and the cached result can be overwritten by the later Hangup response.

**Observed reproduction:** the unchanged L1 harness creates a fake-provider call and real loopback WebSocket. Three HTTP threads post the same generation-0 Connect callback while the socket is connected; after 0.5 seconds the socket closes. Using the **default recovery budget of two**, both authentication modes produced:

```text
fallback responses 3 distinct 3 reconnects 2 state failed provider_hangups 1 exhausted True
signature responses 3 distinct 3 reconnects 2 state failed provider_hangups 1 exhausted True
```

A two-request case with a one-recovery budget also reproduced the failure. No source was altered. In signature mode these are repeated valid requests with the same URL/body; the reproduction uses the fixture signing helper. In fallback mode the attacker still needs the bearer path secret and correlated SID. Overlapping provider retransmissions can also trigger the state-machine failure. No arbitrary number or owner-approval bypass is claimed.

**Smallest fix:** serialize completion processing per call/generation with an async lock or one shared in-flight future. Recheck generation/result/liveness after every wait and before debit/token issuance; all duplicate callers must receive the same completed result. Add a concurrent test that starts requests while the relay is connected, closes it while they wait, and asserts one budget debit, one new token, identical responses and no hangup in both modes. Also test a new relay attaching while older completion requests are still waiting.

This is the original M2 outcome surviving in a concurrency window introduced by the new wait; it is not a separate High finding.

## N1 — Medium, newly introduced: OTP registry lock blocks the service loop and permits late delivery after failure

**Evidence:** `plugin/line/sms/delivery.py:347-359`; `sms/service.py:135-137,181-187`; `engine/service.py:516-517,735-748,346-350`; `engine/sealed.py:40-46`.

Delivery holds the new synchronous `registry.gate` across `call_sink.send`. The production sink schedules `session.send_secret` onto the service loop and blocks waiting for its result. Meanwhile, approval replacement executes synchronously **on that same loop** and blocks in `registry.forget` acquiring the gate. The delivery thread waits for the loop, while the loop waits for the delivery thread's lock. After the sink's ten-second timeout releases the lock, replacement completes and the queued send coroutine can run: `CallService.run` does not cancel it on timeout, and `send_secret` does not carry/recheck the approval generation.

**Observed reproduction:** use the real L1 service and sealed sink with a fixture SMS; pause at sink entry after the registry gate is acquired, queue a replacement approval for another sender, wait for replacement to enter `forget`, then let the sink proceed. A scheduled event-loop marker did not run during the wait. Output:

```text
event_loop_blocked True delivery_result sink_failed elapsed_seconds 10.0 replacement_used used late_dtmf_frames 1
```

The delivery reports failure, the replacement becomes used, yet one DTMF frame arrives afterward. This is a demonstrated service stall and misleading delivery result, with a stale send after the approval transition. The code still went to the same call in this reproduction; no other-call exfiltration is claimed.

**Smallest effective fix:** do not hold a blocking threading lock across a synchronous wait for the event loop when that loop can acquire the same lock. Serialize replacement and generation-checked call delivery on the service loop, with the final approval validation adjacent to enqueueing the secret frame. Give timed-out sends cancellation/invalidation semantics so they cannot later send. Keep browser/worker locking separate. Add a production-sink integration test overlapping replacement with delivery; the existing L4 concurrency tests use a mock browser sink whose completion does not require this loop.

**Trial relevance:** inactive with `sms_source: none`; must be corrected before enabling OTP. H1's original redirect-through-slot bug is fixed despite this new implementation regression.

## Fallback and scope notes

The revised L1 “does not protect” list correctly adds historical admission/cache defenses, setup-frame limitations, first-admission/recovery token races, and that HMAC does not establish freshness. It still overstates Connect's “processed once” guarantee until M2 above is fixed. Its claimed oldest-entry eviction is supported; this is bounded age-based caching, not a security-relevant LRU distinction.

Even after M2 is corrected, fallback is bearer-secret authentication, not a substitute for event signatures. An exposed path secret plus a live SID can enable owner press-1 forgery after the leg answers, and racing initial admission or recovery for a usable relay token. The requested fallback trial accepts that operating mode as its scope; this review does not claim those residuals disappeared or infer any new owner decision.

The two telephone targets must remain the exact owner-controlled allowlist entries. For the stated trial, **OTP off means `sms_source: none`**, not merely `otp_browser_fill: false`: `_register_otp` returns before registering either OTP tool at `engine/hermes_glue.py:354-360`; the browser flag alone leaves call OTP enabled. Browser fill defaults off and its registration/preview gating is tested. Extra origin probes reduce exposure when explicitly enabled but do not establish atomic browser document/frame binding; this is outside the trial.

No new Media Streams route is mounted. `plugin/line/server/ingress.py:291-304` still exposes only health, existing voice webhooks and the ConversationRelay WebSocket. L5 `plugin/line/voice/` was not reviewed or exercised. No claim about Grok/media integration follows from this delta.

Desktop owner authentication/profile isolation is unchanged and was not re-reviewed outside the requested diff. The passing bare-router test still is not deployed-host authentication evidence. This report does not certify the actual tunnel configuration, host profile, provider behavior or receipt of TWILIO-GO.

## Test execution and evidence

The workspace HEAD is still `935ef62a`; it was not checked out or modified. Exported the exact target using:

```sh
git archive dae1b78 | tar -x -C /Users/mac/.hermes/cache/scratch/hermes-line-r1d/disposable
```

Ran from that export with installed `/Users/mac/.hermes/hermes-agent/venv/bin/python`, no dependency installs:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=plugin \
  /Users/mac/.hermes/hermes-agent/venv/bin/python -m unittest discover -s tests/l1 -v

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=plugin \
  PYTHON=/Users/mac/.hermes/hermes-agent/venv/bin/python sh tests/l4/run_tests.sh -v
```

Both commands were launched via the retained `run_suites.py` sandbox wrapper, with an empty inherited environment replaced by a small explicit allowlist. `TMPDIR` and `HERMES_HOME` point under the disposable fixtures directory. `sandbox-exec` denies external network and filesystem writes except fixtures and /dev/null; the export and original checkout are read-only to tests. Only loopback traffic is allowed. Host home data reads are denied except approved scratch, Hermes source/venv and Python runtime; ~/.hermes-line is explicitly denied. Resource limits: 120 s CPU, 240 s suite wall time, 32 MiB per-file size, 256 descriptors, 1024 per-user process ceiling. As in R1, no enforceable macOS RSS/AS limit is claimed. macOS loopback is not a private network namespace; the inspected tests use their own ephemeral listeners and fake adapters, not shared/live services.

| Command | Result |
|---|---|
| L1 unittest discovery | **PASS: 150 tests, 36.210 seconds, exit 0**, no skips reported. |
| L4 lane script | **PASS: 266 tests, 3.783 seconds, exit 0**, including both RealCore tests; no skips reported. |
| Supplemental M2 loopback reproduction, default budget | **Defect reproduced**, fallback and signature; three callbacks consume two recoveries and hang up. |
| Supplemental OTP production-sink lock reproduction | **Defect reproduced**, ten-second loop stall, sink_failed result, one late DTMF frame. |

Evidence retained entirely under the approved scratch root:

- `l1.log`, `l4.log`: full suite output.
- `m2-repro.log`, `m2-default-repro.log`, `otp-lock-repro.log`: bounded supplemental results, no secret values.
- `fixtures/repro_m2.py`, `fixtures/repro_m2_default.py`, `fixtures/repro_otp_lock.py`: reproduction scripts using only sample fixtures and fake provider operations.
- `run_suites.py`: exact environment, sandbox and invocation wrapper.

The reproduction scripts control scheduling with events/barriers; they do not patch source files. The OTP reproduction wraps sink entry and registry-forget entry only to establish the interleaving; actual registry locking, confirmation, service scheduling and sealed sending remain production code. Source and test files in the export remain unchanged.

## Required next step

Correct M2's concurrent completion handling and run its new overlapping-request regression in both modes, plus L1. Keep OTP and browser fill off for the proposed trial; N1 can be fixed separately before OTP is re-enabled. The full existing suite pass is useful evidence, but does not override the independently reproduced call hangup.

No new Critical or High issue was established by this delta review. No live trial or provider action was performed or authorized by this report.
