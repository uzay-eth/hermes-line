# R1 delta 9 — N11 only

Reviewed main **4f25b5b06e27c43250eef7fa1df4279b346ca856**, including L6 **2faacc9** and `docs/lanes/L6.md` round 7.

**Verdict: outbound Grok call-only trial to the owner's Spectrum, maximum three minutes: YES. Subsequent attended inbound Grok trial via Fi: YES.** These verdicts retain delta 8's configuration: `hermes-line-calls`, fallback ingress without Auth Token, OTP off (`sms_source: none`), browser fill off. They clear the N11 blocker for these bounded trials; they do not authorize execution or certify provider billing. The inbound listener's configured window remains explicit; it is not automatically the outbound three-minute cap.

## N11 — Fixed

The two original failure probes now retain the charge instead of settling it at zero.

- `tools/trial/grok_budget.py:238–248,267–296`: elapsed time is booked and persisted synchronously before the first close await. The finally block accounts for incomplete cleanup even on cancellation. Per-socket monotonic accounting prevents duplicate charges for an already counted interval.
- `tools/trial/grok_budget.py:267–271`: concurrent close callers join the original operation through a shield. `close_all` also accounts in its finally block (169–176).
- `tools/trial/grok_budget.py:178–193`: settlement refuses setup or close still in flight. Repeated settlement cannot lower an existing settled charge.
- `tools/trial/grok_budget.py:90–105`: journal writes merge this run's row under a file lock, preserving other runs and retaining the higher prior settled/reconciled charge.
- `tools/trial/run.py:539–546` and `tools/trial/grok_budget.py:119–125,256–265`: after service-loop teardown, synchronous reconciliation accounts abandoned sockets without creating tasks on a closed loop. This also covers a close task cancelled before its first execution.

No new confirmed security finding was identified in this N11-only review. Conservative fallback may overestimate usage after interrupted cleanup; that is appropriate for a spending guard and does not release credit prematurely.

## Independent probe reruns

I copied my delta-8 probes into this review's scratch fixtures, preserving their failure setup and changing only zero-charge assertions to require at least $0.16 and nondecreasing settlement. The 120-second duration is a shifted timestamp, not a paid or 120-second session.

`probe_delta8.py` exited 0:

```
N9 cancelled_setup_socket_closed_after_call_close: True
N10 private logger authorization absent, including forced DEBUG: True
N11 concurrent close/finish: persisted USD=0.16027521
N11 cancelled close: persisted USD>=.16 for simulated 120-second connection
```

`probe_real_close_shutdown.py` exited 0:

```
real_loopback_transport_closing: True
simulated_120_second_connection_persisted_usd: 0.16000135
```

The latter uses the actual websocket connector against loopback and cancels both outer and nested close tasks, matching the delta-8 reproduction. The fake concurrent-close probe reaches the adapter's 0.2-second close timeout before settlement; it does not by itself prove refusal while close remains pending. The new unit test below directly exercises that boundary.

## Crash reconciliation at the next preflight

`tools/trial/run.py:823–834` acquires the global run lock before calling `reconcile_crashes`, before voice-settings recovery or execution of the new command. Thus a new preflight cannot reclassify a still-running harness invocation as crashed.

`tools/trial/grok_budget.py:45–63` locks the ledger, changes pending rows to `reconciled_crash`, and keeps the maximum of the recorded debit and the worst-case reservation plus durably recorded text inputs. It does not refund an interrupted reservation. A repeated reconciliation leaves the row unchanged. Malformed ledger data raises instead of resetting it. The normal admission check continues to sum these rows before reserving a new run. This remains an estimate using the configured rates, not an authenticated provider balance.

The regression `tests/l6/test_n11_accounting.py:128–165` genuinely launches a subprocess, creates a reservation and text debit, opens a fake session, and exits through `os._exit`. It then invokes `main()` as a mocked preflight and checks **$0.254**, no pending rows, and `reconciled_crash` before the mocked execution body is entered. It also checks repeated reconciliation is a no-op. This passed; no real preflight or credential read was performed.

Other new tests exercise the missing claims directly:

- Lines 36–54: close_all waits; finish refuses an in-flight close and retains the reservation; repeated cleanup does not double-book.
- Lines 56–79: cancellation, fallback settlement, and a late close cannot reduce the charge or overwrite a newer reservation.
- Lines 81–100: STT cancellation is charged; unresolved setup cannot settle.
- Lines 102–126: a real event loop is torn down before close starts; Runtime's synchronous reconciliation still accounts the connection.

## Commands, results and limits

I exported the exact main hash into `disposable/`; the original checkout was unchanged. All writes were confined to this review's scratch directory. Execution reused delta 8's offline sandbox: external networking denied, loopback allowed, scratch-only writes, clean environment and scratch HOME/TMPDIR, bytecode disabled, explicit denial of the real `/Users/mac/.hermes-line`, and CPU/process/file-size/descriptor/wall-time bounds. Host loopback was used; no separate network namespace or hard memory limit was added. No provider, phone, tunnel, gateway or real-key access occurred.

Interpreter: `/Users/mac/.hermes/hermes-agent/venv/bin/python`; working directory: this review's `disposable/`. All commands ran through `run_checks.py` and `runner_prefix.py` under `sandbox-exec`.

| Command | Result | Evidence |
|---|---|---|
| `python ../fixtures/probe_delta8.py` | Exit 0 | `probe.log` |
| `python ../fixtures/probe_real_close_shutdown.py` | Exit 0 | `real.log` |
| `python -m unittest discover -s tests/l6 -v` | **51 passed**, 1.365 s | `l6.log` |

Earlier R1 findings and live transport/provider behavior were not re-audited. N9/N10's original probes still pass here; their broader delta-8 assessment remains unchanged. No Critical/High findings, remaining N11 fix, or owner decision is required for this scoped clearance.
