# R1 delta 14 — L6 hold command

**Harness verdict: NO.** Fix N18 and N19 before the live hold trial. Both are Medium findings; no new Critical or High finding was established. This verdict is independent of the pending L1 round-18 fixes for N16/N17.

Reviewed main `30bf199bd7b3fb78d9402fe95503040827b33c04`, including L6 `8e8a9c2195807a37f336e0b3dad0087cbc1efe47`, from a disposable export. Main advanced to `0ebf9068772f6e6dc3dbf22562681b4fd9949b26` during review; `tools/trial/` and `tests/l6/` were unchanged between these refs. References below use repository-relative paths in the reviewed export.

Read the specifically permitted `~/.hermes-line/approved_destinations.json`: mode 0600, one Delta `+18002211212` entry, expiry `2026-11-12T00:00:00Z`, 45-minute cap, 20-second owner ring, checked-bag-fee fallback and the stated prohibitions. It was unexpired on the review date, November 5. No other file in that directory was read. No real credentials, provider calls, tunnel, gateway or external network were used; the checkout and Git were not changed.

## Findings

### N18 — Medium — an ambiguous create is absent from crash recovery and can be reported as successfully cleaned up

**Source:** `tools/trial/hold.py:214–235` writes an attempt journal in the individual run directory before creating the call, but writes the startup-visible `hold-recovery.json` only after receiving a valid CallSid. `hold.py:258–282` cleans only known SIDs and deletes an existing recovery marker whenever those operations succeed. `hold.py:445–448` returns immediately if that marker is absent; startup at `tools/trial/run.py:1009–1014` does not inspect unresolved per-run attempt journals.

**Failure:** the provider accepts a business create, but the response times out, contains no usable SID, or the process dies before saving the returned SID. The attempt journal exists, yet no startup recovery marker identifies the unresolved create. Cleanup sees an empty SID list and succeeds. The next invocation's recovery returns “nothing pending” and does not block another run while that call may still exist. The analogous owner-create window can leave an unresolved owner leg while cleanup ends the known business leg and deletes its marker.

**Reproduced offline:** `fixtures/probe_hold_guards.py` makes the real-dispatch branch's fake HTTP boundary accept the business create and then raise `ProviderError('Twilio unreachable: TimeoutError')`. The production adapter records its attempt, has no known SIDs, returns an empty cleanup-failure list, and `recover()` returns `False` without requesting credentials. The fixture observes precisely the application's response-loss behavior; it does not prove the duration of an actual unknown provider leg.

The provider TimeLimit is a useful last bound, and the lane notes acknowledge create ambiguity. The defect here is the missing durable unresolved state and false successful-cleanup result, not an expectation that a crashed process can instantly hang up an unknown SID. Its effect is confined to the approved destinations and bounded trial, hence Medium, but it violates the requested recovery/no-orphan readiness condition.

**Smallest fix:** persist a startup-visible unresolved-create record before every Calls request, with role, approved destination, task/call identity, attempt time and absolute deadline; durably update it when the SID is known. Never erase an unresolved attempt merely because all *known* legs ended. Recovery must reconcile it through narrowly scoped provider evidence or refuse new calls pending coordinator resolution/verified expiry. Report unresolved cleanup distinctly. Do not automatically repeat the create.

**Tests:** `tests/l6/test_hold.py:132–142` really proves no second HTTP create from the same adapter after a timeout. It does not call recovery or test a new process. `test_hold.py:151–165` tests recovery only when a known SID is already in the marker. Add response-loss and crash-window cases before SID persistence, for both roles, followed by cleanup and a fresh startup.

### N19 — Medium — shutdown during the balance check can still dispatch a new call

**Source:** `hold.py:195–200` checks `self.stopped` before approval and a blocking balance read. After `budget.check(force=True)` at line 222, the code checks remaining time and the human event, then creates the call at line 231 without checking stop again. `dispatch_window()` sets `stopped` on exit (`hold.py:386–391`), while `cleanup()` also sets it and traverses the SID list (`hold.py:258–282`); neither coordinates with the create lock. The short-call adapter already has a post-balance cancellation check at `run.py:205–208`, which this new adapter omits.

**Reproduced offline:** the second probe starts a create and pauses its budget reader. It exits the real `dispatch_window`, confirms `stopped=True`, and calls production cleanup, which returns success with no known legs. Releasing the balance reader then produces a Calls form and a live fake leg. The probe uses production adapter/window/cleanup methods with controlled threading; it is not a full OS-signal/Twilio integration test.

This is reachable when Ctrl-C/down/teardown occurs during a balance request. A provider call can begin after the harness has closed dispatch, and cleanup can miss a subsequently returned SID. Runtime teardown is not a sufficient synchronization guarantee: `CallService.stop()` has bounded waits and suppresses shutdown errors (`plugin/line/engine/service.py:239–254`); the server closes its loop without joining the default executor (`plugin/line/server/runner.py:81–88`). The resulting risk is an unwanted approved-destination ring and potentially a leg surviving cleanup, not arbitrary-number dialing.

**Smallest fix:** recheck cancellation after all blocking pre-dispatch work, and serialize the final stop/dispatch commitment. Track and drain in-flight creates before cleanup can declare success; any late returned SID must be ended and remain recoverable if that fails. Combine this with N18's durable unresolved record. Revalidate expiry at that final commitment too, since a balance read can cross the approval's expiry. A stop after the HTTP request has already committed must be reported and handled as an in-flight create, not “never dialled.”

**Tests:** the current role/deadline/cleanup tests do not stop a worker during a balance read or during a Calls response. Add the reproduced interleaving and a response arriving after shutdown begins, asserting no new request when stopped before commitment and eventual cleanup/recovery when already committed.

## Requested controls

| Control | Assessment and source |
|---|---|
| Exact approved business and configured owner only | Good within one invocation. `hold.py:23–55` requires canonical US-form input, policy acceptance, one complete unexpired approval row and mode 0600. `hold.py:198–213` binds task, call, role and exact destination, rejects the plugin line/Bora, and re-reads approval at each live dispatch. `run.py:163–174` keeps allowlist-only, Bora blocked, approval required, disclosure on and recording off; `hold.py:402–403` replaces only the allowlist with the business and Spectrum owner. The real `place_call` still consumes an engine-issued single-use permit (`plugin/line/engine/telephony.py:90–99`). |
| Approval expiry/revocation | Rechecked before each live create and while running (`hold.py:209–213,243–246`). Changed approval rows refuse dispatch. Expiry is checked with wall time; total duration uses monotonic time. A blocking balance request separates the approval check from the provider request; include a final expiry check in N19. |
| No retry/redial | The role attempt is consumed before the balance/create operation; failures stop that adapter (`hold.py:201–240`). At most one business attempt and one owner attempt. This does not resolve N18 across crashes/new invocations. Reconnection of the same Relay leg is not a new Calls create. |
| 45-minute global cap and leg limits | Start is recorded at the first create attempt, before funds/API latency (`hold.py:215–218`). Remaining lifetime is recalculated after the balance check and passed as provider TimeLimit; owner creation requires at least its 20-second ring plus five seconds (`hold.py:222–230`). `tick()` refuses at 2700 seconds. The run loop checks every 0.2 seconds (`hold.py:422–429`), with the engine watchdog as another bound. This is a shutdown threshold, not proof of exact remote termination at second 2700: synchronous network reads and teardown add latency, and process death leaves provider limits/recovery as backstops. |
| 60-second balance guard / $4 stop | Correct fail-closed behavior for read errors under the serial budget lock (`hold.py:116–138,243–256`). Fresh admission reserves 45 minutes plus 90 seconds and retains $5. Subsequent projection takes the lower of the initial-balance estimate and latest-balance estimate, including 90 seconds exposure; at or below $4 it raises. A failed/malformed/non-USD/nonfinite balance read does not reuse an old balance and continue (`run.py:143–149`). It aborts the run into cleanup. The normal 60-second cadence is subject to request latency, not a hard realtime guarantee. |
| Pricing and project envelope | Pricing failure intentionally uses S2 planning rates ×1.5 (`hold.py:19–20,159–174`). Charging both legs and two conference participants throughout is conservative *relative to those inputs*, not proof that unverified current rates are bounded. No current pricing was queried here. The new code has no cumulative Twilio project-spend ledger or $20-cap admission calculation; account balance is not project spend. The coordinator still needs a documented remaining project envelope and acceptable rate basis before a live call. The $4 balance rule does not expand the original budget. |
| Normal exit / Ctrl-C / crash cleanup | The context/finally path closes dispatch, tears down runtime, then ends known legs and the exact generated conference (`hold.py:310–326,413–440`; `run.py:656–676,729–762`). Foreign conference rows are refused, and cleanup failures keep a known-SID marker for the next startup. N18/N19 prevent an “every exit” assurance. A second interrupt, SIGKILL or network outage can also interrupt remote cleanup; a reliable persistent marker is therefore essential. |
| Live JSON order | Good for the observed transitions. `Events.attach()` synchronously observes the stored `human_detected` state (`hold.py:82–95`); emission flushes/fsyncs the file and flushes stdout before adding it to `sent` (`hold.py:68–80`). Owner dispatch requires that event (`hold.py:229–230`). The real-engine dry test checks it at the actual fake Calls boundary. `business_dialing` and `owner_ring` are emitted after their create returns, so they indicate acknowledged creation, not the exact request-start timestamp. |
| Evidence masking | New live rows contain event/time, masked SID suffixes and controlled outcomes. Transcript/engine exports go through `api.masked` (`hold.py:324–325`); `run.py:521–532` masks phone numbers and fallback bearer URL components. Provider exports select SID/status/duration/price fields rather than raw responses (`run.py:852–874`). Private recovery/cleanup files retain full SIDs for operations, not API keys or webhook URLs. Full CallSIDs are also intentionally retained in existing evidence; they are identifiers, not authentication secrets. No new credential-bearing evidence path was established. |

The five-minute menu stop uses wall-clock event timestamps and only applies until an `on_hold` event has appeared (`hold.py:425–428`). The two-unsuccessful-menu-retry rule remains task guidance, not a counter in this harness. These limits are weaker than a universal menu-state watchdog; the global monotonic cap remains. N16/N17 and any L1 round-18 solution were not re-reviewed in this harness delta.

## Validation and limitations

Command, run in the disposable export with bytecode disabled and `PYTHONPATH=plugin`:

```text
python -m unittest discover -s tests/l6 -v
76 tests in 4.113 s — OK
```

This includes the 11 hold tests and the actual local-engine dry flow: menu → hold → human → owner ring timeout → approved fallback question → end. Its clock is accelerated; it is not a live 45-minute soak. The tests exercise role limits, attempt limits, remaining TimeLimit, polling arithmetic, balance failure, exact-conference cleanup, known-SID recovery and event ordering. They do not exercise N18/N19. See `l6.log`.

Additional command:

```text
python ../fixtures/probe_hold_guards.py
exit 0 — both unsafe behaviors reproduced; see probe.log
```

Its assertions establish the defects; exit zero is not a security pass. Inputs use synthetic numbers/SIDs and a future fixture approval; no live approval was consumed or modified. The real approval file was inspected separately, read only.

Execution used the same expressly permitted disposable/loopback arrangement as delta 13: `sandbox-exec` denied external networking and real line-state reads, made target/toolchain read-only, and confined writes to fixture scratch. Environment was allowlisted with scratch HOME/TMPDIR/HERMES_HOME. CPU limit 120 seconds, maximum file size 32 MiB, 256 descriptors, 1024 processes; suite wall timeout 180 seconds, probe timeout 30 seconds. Host loopback was allowed, without a separate network namespace or aggregate memory/disk quota. No installs occurred. Security-audit guidance was applied within the coordinator's scoped review and artifact limits.

This is an offline harness review, not verification of current carrier rates, provider termination semantics under every fault, actual account budget, or deployed gateway/tunnel behavior. Fix N18/N19 and add their regressions; retain a separate L1 round-18 verdict and coordinator budget/preflight evidence before the trial.
