# R1 delta 8 — Grok setup, credentials and trial spending

Reviewed local main **d01559671df7441c03cae15e95774b6e52ce0792**, including L5 **07263e1** and L6 **91c0152**. Date: 2026-11-05.

**Verdicts: (a) outbound Grok call to the owner's Spectrum, maximum three minutes: NO; (b) subsequent attended inbound Grok call via Fi: NO.** N9 and N10 are fixed, but the new trial ledger can release a reservation without accounting for connected time during shutdown (N11, Medium). Fix that accounting boundary first. No Critical or High finding was identified in this scoped delta.

These verdicts concern the supplied trial harness, `hermes-line-calls`, fallback ingress without Auth Token, `sms_source: none`, and browser fill off. They are not authorization to make calls. The inbound harness permits 1–10 minutes; the coordinator must select the intended attended window. This review does not silently interpret that setting as a three-minute cap.

## Scope and execution boundaries

The checkout remained unchanged. Its checked-out revision was older than main, so I exported the exact main hash above with `git archive` into this review's `disposable/` directory and reviewed/tested that snapshot. File:line references below refer to that snapshot. I read L5 section 6, L6's new transport/ledger notes, the relevant source and tests, and the installed Hermes/websockets implementation where needed. The additional N8 stop-intent changes in main were inspected for context, not subjected to a new full R1 review.

All test execution used the existing Hermes Python 3.11 environment under `sandbox-exec`: external networking denied, loopback allowed, writes restricted to this review's `fixtures/`, clean allowlisted environment and scratch HOME/TMPDIR, bytecode writes disabled, and `/Users/mac/.hermes-line` explicitly denied. CPU, process, descriptor, file-size and subprocess wall-time limits were set. This uses host loopback, not a separate network namespace; no hard memory limit was imposed. No packages were installed. No real credentials, provider endpoints, phone calls, gateway attachments or tunnels were used. Key-file tests used synthetic files under scratch; nothing under the real `~/.hermes-line/` was read.

## Previous findings

### N9 — Fixed: setup socket ownership and cancellation

Evidence:

- `plugin/line/voice/provider.py:196–216`: `connect_owned` retains the handshake task behind a shield. On failure/cancellation it closes an already returned connection, or cancels the task and attaches a callback that closes a late successful result.
- `plugin/line/voice/grok.py:88–108`: both the initial `session.update` and readiness wait are inside cleanup; cancellation closes the adopted socket and propagates.
- `plugin/line/voice/stt.py:87–96`: the helper is shared, and socket adoption/reader creation have no intervening await.
- `plugin/line/voice/controller.py:543–567,609–632,863–906`: providers/transcribers are tracked before setup. Setup runs in a separate task; close cancels that task, not its hosting stream reader. Failed, cancelled and post-close results are discarded.

The original cancelled-setup probe was rerun with its assertion changed to require closure. Result: `cancelled_setup_socket_closed_after_call_close: True`; no adopted provider remained. The helper adds no retry loop.

The five N9 test methods in `tests/l5/test_review_r1d7.py:61–188` exercise the actual bug and adjacent failures: original probe, close during readiness with the stream reader surviving, initial-send failure, late successful handshake after cancellation for both adapters, and STT handshake/adopted-stream teardown. These are substantive regressions, not assertions about implementation names. All passed. A deliberately cancellation-resistant connector can finish after `close()` returns; its late result is closed by the callback when the event loop runs it. This is eventual cleanup, not a promise that arbitrary injected connectors have synchronously ceased at return.

### N10 — Fixed: outgoing Authorization debug logging

Evidence:

- `plugin/line/voice/provider.py:154–186,219–225`: the actual default websocket connector receives a dedicated INFO-level logger, independent of root/websockets DEBUG configuration. Its filter redacts Authorization/bearer records, clears their arguments and attached exception fields.
- `plugin/line/voice/grok.py:88–108` and `plugin/line/voice/stt.py:87–92`: connection errors exposed by these adapters contain only the exception type; failed Grok setup has a fixed message. Exception chaining is suppressed.

I adapted the old raw `ClientProtocol` logging probe to pass the **production private logger**. The library's behavior with an unrelated unsafe logger has not changed and is not the intended fix. With root DEBUG, and then with the private logger forcibly lowered to DEBUG, the synthetic Authorization value was absent; the forced-DEBUG case emitted a redaction marker.

The three N10 methods in `tests/l5/test_review_r1d7.py:205–309` test successful Grok/STT and rejected HTTP 401 handshakes using the real websocket client and a loopback server. They establish that the synthetic key was actually sent, then check captured logs and formatted exceptions. The explicit private-logger DEBUG test also passes. These exercise the reported leak. They do not prove that arbitrary future exception formatting, a server that deliberately echoes credentials, or application code logging a raw secret would be safe.

## New finding

### N11 — Medium: shutdown can settle connected time at zero and release the spend reservation

**Evidence:** `tools/trial/grok_budget.py:163–182` marks a metered socket `closed` before awaiting underlying close, but increments connected seconds only **after** that await. `close()` calls made meanwhile return immediately. `close_all()` at lines 108–109 consequently does not necessarily wait for accounting already in flight. `finish()` at lines 111–122 checks only the `closed` flags, writes a settled charge from the current counters and becomes permanently idempotent.

The harness uses these operations during teardown at `tools/trial/run.py:524–542`, and again calls `finish()` in its outer finally at lines 856–858. Server teardown cancels **all** remaining loop tasks at `plugin/line/server/runner.py:81–86`, including nested close tasks. A cancellation before the counter update skips it: `MeteredSocket.close` catches `Exception`, not cancellation, and has no accounting finally block.

**Concrete failure:** start a metered connection, begin close, then cancel the outer close and its nested websocket close as loop teardown does. The transport has been aborted, so `closed` is true, but the seconds counter remains zero. Subsequent `close_all(); finish()` settles the run at zero and releases the durable reservation. Alternatively, with a delayed close, a concurrent finish can permanently persist zero before the first close later updates the in-memory counter. This understates cumulative spend and can admit later runs against an incorrect balance. It affects the owner's budget boundary; it does not bypass destination/consent gates or show that the aborted connection remains billable.

**Reproduction:** `fixtures/probe_delta8.py` reproduces both delayed-close settlement and cancelled-close accounting with fake sockets. `fixtures/probe_real_close_shutdown.py` uses the actual websocket connector and a loopback websocket server. Only the connection start timestamp was shifted to represent 120 seconds without waiting or spending money. It reported:

```
real_loopback_transport_closing: True
simulated_120_second_connection_persisted_usd: 0E-8
```

At the ledger's configured realtime rate, that simulated interval should contribute approximately $0.16. No audio or real key was used.

**Calibration:** an earlier loopback probe cancelling only the outer close task did **not** reproduce this: it correctly settled approximately $0.16. The confirmed real-transport case cancels both pending close tasks, matching the server's final task cancellation pattern. The defect depends on shutdown timing or a delayed close; it is not a claim that every normal exit loses its charge.

**Smallest fix:** once transport abort establishes the stop time, book elapsed time exactly once **before the first await** (or guarantee the same accounting in a cancellation-safe finally). Separate “transport closed” from “close/accounting complete,” and have concurrent callers join a single cleanup operation. `finish()` must not release a reservation while setup/cleanup remains unresolved; retain the conservative reservation if completion is uncertain. Add regressions for concurrent close/finish and cancellation of both outer and nested close tasks.

**Existing tests miss this:** `tests/l6/test_grok_budget.py:25–34` assigns counters directly before settlement. Lines 66–83 use an immediately completing `AsyncMock.close` and wait beyond the cutoff before finishing. Neither suspends close across settlement nor cancels cleanup. Passing them does not establish cancellation-safe accounting.

## Trial runner key and cap checks

The new key path is appropriately scoped: `tools/trial/run.py:94–112` requires mode 0600 and a single nonempty line, reads once, retains the value in a private mapping, and installs/resets a Hermes secret scope around each getter invocation. It does not put the newly loaded key into `os.environ`, the ACP session specification, a ledger or evidence object. `raw = ''` drops one reference; the closure intentionally retains the credential for the run and does not securely erase process memory. The installed `agent/secret_scope.py:154–166` confirms the binding/reset behavior. Top-level unexpected errors are rendered as their type rather than arbitrary exception text (`run.py:898–903`). The synthetic key-file regression at `tests/l6/test_grok_budget.py:49–62` passes, including deleting the file after loading and retrieving the key again.

The **entry cap is enforced** for a valid ledger: a process-wide harness flock covers reservation and execution (`run.py:821–858`); `GrokRun` checks previous settled/pending amounts plus the proposed reservation and persists it before `execute()` loads Grok credentials (`grok_budget.py:26–64`, `run.py:851–862`). Pending crash reservations are retained. Numeric amounts reject nonfinite/negative values, and malformed ledger reads raise rather than resetting the balance. My additional CLI probe seeded $1.70, requested a three-minute run, and verified rejection before `execute`/credential/provider work. Existing voice-settings crash recovery can precede this check; that restores prior Twilio state rather than starting the requested Grok run.

Realtime and STT factories both use the metered connector (`run.py:501–510`). It allows at most one active connection per kind and shares a single deadline across reconnects (`grok_budget.py:79–106`). Cutoff aborts the underlying transport, and text items are charged durably before sending (`grok_budget.py:69–77,137–152,169–172`). These are useful bounds, but N11 makes subsequent cumulative admission unreliable.

The ledger is explicitly a connected-time/text estimate, not provider billing or a provider-enforced account cap. Pricing, invoice granularity and prior usage outside this harness were not verified. Nothing in this review certifies a current live balance. The two requested trial paths share the faulty settlement code; neither receives a readiness exception.

## Commands and results

Working directory for target commands: this directory's `disposable/`. Interpreter: `/Users/mac/.hermes/hermes-agent/venv/bin/python`. Commands ran through the offline sandbox described above; wrappers and logs are retained beside this report.

| Command | Result |
|---|---|
| `python -m unittest discover -s tests/l5 -v` | **79 passed**, 18.158 s; `l5.log` |
| `python -m unittest discover -s tests/l6 -v` | **45 passed**, 1.264 s; `l6.log` |
| `python -m unittest discover -s tests/l1 -p test_r5_voice.py -v` | **10 passed**, 7.012 s; `l1voice.log` |
| `python ../fixtures/probe_delta8.py` | Exit 0: N9/N10 fixed; two N11 reproductions confirmed; `probe.log` |
| `python -B ../fixtures/probe_cap.py` | Exit 0: over-budget CLI rejected before execution; `cap.log` |
| `python ../fixtures/probe_real_close.py` | Exit 1: hypothesized outer-only cancellation loss **not reproduced**; actual charge approximately $0.16; `real-close.log` |
| `python ../fixtures/probe_real_close_shutdown.py` | Exit 0: all-pending-close cancellation reproduces N11 with real loopback transport; `real-close-shutdown.log` |

The targeted L1 tests cover transport choice, media/relay token separation and delayed digits after owner handoff. They do not constitute a full rerun of all earlier R1 authorization findings. No live compatibility, Grok voice quality, forwarding configuration or actual billing claim is inferred from these offline results.

Required before either trial: fix N11, retain fail-closed accounting for interrupted cleanup, rerun its reproductions plus L5/L6. No owner decision is needed to specify that fix.
