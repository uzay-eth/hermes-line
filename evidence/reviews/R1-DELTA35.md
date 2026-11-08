# R1b delta 35 — current owner-line trial

**Verdict: YES for one attended, keypad-only owner-line trial in which Can asks Hermes to call his own Fi line.** Use the reviewed `listen` path, ConversationRelay, OTP off, and its strict two-owner-number allowlist. This is not clearance for general business calls, transactional tasks, spoken PIN entry or a broader allowlist. No live action was performed or authorized by this review.

## Blocking findings

**None confirmed in the specified trial scope.** Applying the coordinator's threshold, no Critical/High issue or path that wrongly rings/dials and leaks PIN/codes or commits to a transaction was demonstrated for this single owner-to-own-Fi task. Two reproduced Medium limitations remain below; neither involves the intended keypad-only, single-proposal trial flow.

## Revision and review scope

Reviewed clean `main` at `13f0eb5b31c8f3c5f2ba9ceaa6db0461bfda3b35`, containing L6 `117d6b05e1671f44d9985b21f2b81624732741c5`. Reviewed the current owner PIN, speech scrub, proposal/keypad confirmation, approval-ticket and listener dispatch paths against delta 12 and its delta-10/11 probes. Read L1 sections 23–24 and the later four/seven-digit rule, the relevant tests and the L6 change. Separate `test_r14*` and `test_r15*` files are absent: those regressions live in `test_r13_owner_line.py`, as the lane documentation records.

At final verification, main had advanced concurrently to `cf008832f3d7bf0c2da5dbf9dcda15314107d7be` with L3 pane/documentation changes. The checkout was clean, and the diff contains no changes under `plugin/line/engine/`, `tools/trial/`, `tests/l1/` or `tests/l6/`; the reviewed code and tests therefore remain current. The engine snapshot hash also matches current main.

The tracked source was exported into this scratch directory. No tracked Graft index was available; no graph rebuild was run. The previously read project BRIEF and task limits continue to govern this review.

## What was verified

- **PIN entry:** caller-number recognition alone does not grant owner privileges. Keypad PIN verification is required on that session; speech never verifies. Unknown callers cannot invoke `owner_new_task`. Wrong attempts, per-line lockout across redials/restarts, the daily limit and owner-only unlock checks pass. Pause-submitted third failures end the call rather than cancelling their own hang-up.
- **PIN/code exposure:** keyed PIN digits bypass speech persistence. The current L6 test stages a synthetic PIN in a scratch home, reads it through the real secret scope, removes the fixture file, exercises a wrong and a correct entry, and checks exported evidence/logs for the synthetic values. It passes. This does not inspect or attest to the real staged PIN. Listener config disables SMS intake (`sms_source='none'`) and browser OTP filling.
- **Confirmation:** ordinary speech, including yes, cannot approve. Wrong digits and expiry invalidate a proposal. The ticket binds the full destination and task and is consumed once. A new destination now requires its **last seven digits**; the owner's configured number or an earlier actually dialled destination may use four. The fixed read-back tells Can which to enter. Do not reuse delta 12's assumption that every challenge has four digits.
- **Destination scope:** the listener config keeps only the two owner numbers, requires approval, blocks the excluded number and preserves quiet hours. The L6 owner-scope test proves an airline destination remains rejected even if a separate hold approval exists; it neither consults that hold approval nor invokes the provider fake's HTTP boundary.
- **Child and owner legs:** `RingbackAdapter` requires an engine permit, PIN-verified parent event, matching approved task/full destination and a terminal parent before creating the business leg. The listener retains `owner_live_bridge=False`. On this path, Can confirms and then hangs up; the child is not dialled while that parent remains active. A new child session does not inherit owner PIN verification.
- **Ring-back:** the rehearsal performs the current fixed live-person question and clear-yes exchange before the owner leg. The owner must press 1 before conference handoff. Decline/no-answer paths do not retry. Changed destinations, unexpected direct creates, expired windows and duplicate attempts are refused. The happy-path rehearsal verifies exactly the Fi business destination followed by the configured owner destination, bounded time limits, conference termination and completed fake legs.
- **Listener lifecycle:** source review confirms coordinator/minute validation, relay-only restriction, one accepted inbound SID, rejection of additional callers, disabling admission on exit, cancellation, parent hang-up and restoration while ingress remains alive. Runtime shutdown ends sessions before evidence collection. These live tunnel/provider operations were not executed.
- **117d6b0:** the selected masked-error test passes: gate reasons are masked, and arbitrary exception text is not exposed. The current rehearsal uses the actual trial policy and quiet configuration rather than a weaker test policy. The new live call/hold CLI failure paths were read; those full CLI paths were not invoked.

## Medium/Low backlog — separate from the trial verdict

### Medium — confirmation input does not identify a replacement goal or prove read-back delivery

`machine.py`, `owner_new_task` / `_confirm_check`: proposal creation makes the destination-derived keypad challenge active before read-back delivery. Replacing a proposal resets its buffer/generation, but a new goal for the same destination has the same challenge.

Adapted delta-12 reproduction: with read-back transport withheld, propose a task, replace it with a different goal for the same number, and key the earlier proposal's digits. The ticket is issued for the replacement task. The test checks the ticket's task ID and confirms no read-back reached the simulated relay. This reproduces early approval of an unread replacement, not an actual provider transaction.

The original distinct-number/four-digit collision case now fails closed because the new destination requires seven digits. That improvement does not make destination digits a unique per-proposal challenge or bind them to heard task content. General use should require a fresh challenge for the full proposal, or equivalent confirmation on the owner surface, and reject stale queued input after replacement.

**Why backlog for this trial:** the specified action is one attended request to the owner's own Fi line, with no replacement task or business transaction. Its two allowlisted destinations have different confirmation suffixes. This clearance must not be extended to conflicting proposals or transactional work.

### Medium — slowly spoken PIN fragments can reach model history

`machine.py`, `_window_speech`, and `owner_line.py`: the immediate two-frame and semicolon PIN probes are scrubbed. A first fragment released after the bounded hold can still reach the fake Hermes driver's history before the continuation arrives. Later transcript redaction does not recall earlier model input. The adapted delta-12 probe reproduces both retained driver input and repaired stored rows. The rolling window remains bounded.

General assurance against spoken-PIN leakage would require suppressing credential-like speech rather than relying on reconstruction before a deadline, with explicit handling of all relevant sinks.

**Why backlog for this trial:** the owner will key the PIN; the tested keypad path does not send it to Hermes or evidence. The finding concerns mistakenly spoken, delayed fragments and remains the limitation previously accepted for the keypad-only trial. No assurance of universally secret spoken PINs is given.

No additional Low finding is asserted. Existing comments mentioning “spoken yes” or “last four” are stale in places; executable confirmation uses keypad input and the four/seven-digit rule described above.

## Execution and results

The old scripts used loopback HTTP/WebSockets. To satisfy this round's **no network** limit, `fixtures/memory_transport.py` substitutes an in-memory Starlette ASGI transport for the server/client socket boundary. Production ingress, signature/account/path checks, setup correlation, engine/service/store and fake provider adapter remain active. The service runs on the ASGI client's event loop. A synthetic MCP port supplies endpoint metadata for the fake ACP scope test; no MCP socket is bound or contacted. This validates logic, not real sockets, carrier behavior or acoustic playback.

| Run | Result |
|---|---|
| `owner_tests.py`: current `test_r13_owner_line.py` | **27 passed** |
| `extra_tests.py`: current `test_r12_live2.py` | **7 passed**, including fake ACP scoped-tool briefing |
| `suffix_tests.py`: round-16 four/seven-digit rules | **3 passed** |
| `l6tests.py`: ring-back rehearsal, owner PIN staging/export, masked-error unit test | **14 passed** |
| `probe10_11.py`: adapted original redial/pause-hang-up sequence | Pass; redial remains locked and pause-triggered hang-up completes |
| `old_probe.py` / `probe12.py`: adapted delta-12 historical probe | Pass; old spoken-approval and immediate split-PIN failures closed; two backlog behaviors reproduced |

Total selected author tests: **51 passed**. All final runner subprocess exits are 0. The delta-10 greeting/full-spoken-PIN, lockout and pause cases are also retained as inverted assertions in the current owner suite. Removed timer-arming behavior is tested through the replacement keypad contract, not by restoring obsolete APIs.

Probe adaptations: the current ticket is a dictionary rather than a tuple; the new-destination challenge is seven digits; the collision probe now asserts refusal of the old four-digit entry and separately tests same-number goal replacement. An initial fake ACP test failed because the in-memory server advertised no MCP endpoint; supplying synthetic endpoint metadata made it pass without changing production code. No real gateway/model was used.

Artifacts: [run-results.txt](run-results.txt), [owner_tests.log](owner_tests.log), [extra_tests.log](extra_tests.log), [suffix_tests.log](suffix_tests.log), [l6tests.log](l6tests.log), [old_probe.log](old_probe.log), [probe10_11.log](probe10_11.log). The scripts and exact source export are retained here.

Reproduce from this scratch directory:

```text
/usr/bin/python3 runner.py owner_tests old_probe probe10_11 l6tests extra_tests suffix_tests
```

The runner prints child exits; its own exit is not an aggregate verdict. No full L1/L6-suite or live provisioning/restoration pass is claimed. The review cannot establish that the real PIN is staged, that the gateway is ready or that live carrier DTMF behaves as the fake transport does.

## Boundaries

All execution used a sandbox denying networking, including loopback, and reads of the real `~/.hermes-line/`. Scratch HOME/TMPDIR/HERMES_HOME and synthetic credentials were used; bytecode is disabled and hermes is a nonfunctional stub. Test writes are restricted to scratch fixtures and `/dev/null`; exports/reports stay in delta-35 scratch. The real approval file was not needed or read. No real PIN, provider, gateway, model, call or private runtime setting was accessed. No repository changes, commits, pushes or installations occurred.
