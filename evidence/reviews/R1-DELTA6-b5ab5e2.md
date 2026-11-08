# R1 delta 6 — N7

Reviewed `b5ab5e23bc01a622f5e3afb327330ea8c6b6c891`, including L1 section 19 and `tests/l1/test_r10_n7.py`.

**Verdict: YES for the second outbound call-only trial to the owner's Spectrum**, with the same delta-5 configuration: OTP off (`sms_source: none`), ConversationRelay, fallback ingress without Auth Token, restricted `hermes-line-calls` profile, owner-phone allowlist and protected destination blocked. **N7 is fixed for the demonstrated startup cancellation failure.** No Critical/High finding. One Low validation-order defect below does not permit an unwanted call and is not a blocker for this attended trial. This review does not authorize a call or verify live settings.

## Evidence and scope

Source references below are relative to the reviewed commit. The checkout remained unchanged. I used an exact git-archive export under this report's `disposable/` directory. All fixtures and reports are under the requested delta-6 scratch directory. Execution reused the delta-5 sandbox: external network denied, scratch-only writes, empty allowlisted environment, scratch HOME/HERMES_HOME/TMPDIR, no bytecode writes, bounded execution and authorized loopback fixture traffic. No Twilio, phone, real gateway/model, key access or reads under `~/.hermes-line/`. The existing security-audit guidance was applied within the requested narrow scope.

## N7 reproduction: fixed

The prior `probe_cancel.py`, with its final assertion changed from one provider creation to zero, passes for both `hang_up` and `cancel`:

```text
hang_up: zero calls; stop result ok, stopped_before_dialling=True; state ended
cancel:  zero calls; stop result ok, stopped_before_dialling=True; state ended
```

`service.py:632–643` records an intent before waiting behind the approval command. `_record_stop_intent` and `_claim_dispatch` share `_stop_lock` (`:655–689`). The latter removes the call from `_starting`, consumes the intent, and only permits the subsequent authorization/provider path if no stop won. `_dial` handles a winning stop at `:819–825,873–875`: terminal session, canceled task, event and dropped grants, with no provider creation. The new test checks eventual driver/session/token cleanup too.

The new regression tests genuinely address the old test's weakness: slash hangup/deny and service-queue cancel are exercised while startup is paused. They assert zero provider creations, not merely a terminal final state. The Desktop-named test uses `svc.submit(source='desktop')`, not an HTTP Desktop request; the store-only test models that separate queue path.

## Race boundary and result truthfulness

The guarantee ends at **dispatch claim**, not at the exact instant an HTTP provider request starts. That distinction is accurately documented in section 19.

I paused the real final `gate.authorize` after `_claim_dispatch` had returned, before any fake provider creation. A concurrent owner Hang up returned queued with no stop-before-dial promise. After resuming, one call was created and ended; neither the immediate nor completed stop result claimed `stopped_before_dialling`. See `post_claim.log`. The supplied test also checks a stop while provider creation itself is in progress.

Within one service process, the shared lock establishes which operation wins: an intent recorded while the call is in `_starting` prevents dispatch; after removal, `submit` cannot promise that result. The original consent and destination gates still execute after startup. The r9 consent/policy regressions all passed.

For another process, `Store.pending_stop` reads queued/running commands (`store/db.py:385–398`). I used a separate Python process and separate Store connection to commit a desktop hangup while startup was paused, verified the service had no in-memory intent, then resumed startup: zero calls and a stopped-before-dial result. See `process.log`. The repository test writes through the existing Store object instead, so my probe additionally checks actual cross-process visibility.

There is **no cross-process lock spanning the database read and provider dispatch**. A stop committed after the pending-stop read may lose, even if it precedes the provider request; its submitter receives queued status, not an early guarantee. It is then processed as a normal hangup. The implementation should not be described as “every stop before the network request wins.” This documented cutoff is acceptable for the narrow trial.

For valid commands targeting the same call, I found no path that both creates that call and reports it stopped before dialling: `_stopped_before_dial` is populated only on the branch that returns before dispatch (`service.py:687–688,873–875`). A successful startup stop leaves the task canceled, so a repeated approve cannot restart it. No tool/API operation clears the intent or resets that canceled task to awaiting approval. A late cancel now hangs up an already-dialled task instead of claiming nothing was dialled (`:723–728`). Standard idempotency still returns the original command result: callers must not reuse a key for a different operation/call; it is not fresh confirmation of the second target.

## N8 — Low: rejected commands can still have a stop side effect

**Evidence:** `service.py:632–636` records the intent before enqueue validation; `:659–665` checks owner source only for `deny`; `:711–717` rejects invalid call-agent actions and stale revisions later. `store/db.py:396–397` similarly filters source only for deny.

Two independent fixtures demonstrate the mismatch:

1. Submit `hang_up` with `source='call-agent'` during startup. The call is stopped with zero provider creations, but its queued command ultimately returns `not an in-call action`. This bypasses that executor restriction for the stop side effect.
2. Submit an owner hangup with stale `expected_revision=0`. Enqueue raises `StaleRevision`; no valid stop command is inserted, but the previously recorded in-memory intent remains. Approval then reports stopped before dialling and creates zero calls. The first uncaught fixture run exposed the exception; the retained corrected fixture catches it and verifies the subsequent effect.

This is a cancellation/availability and validation-consistency defect, not a way to dial, approve, release OTP, or un-stop a call. The current in-call MCP tool list has no arbitrary `submit` action, and the normal Desktop store path validates revision before insertion. I did not establish a remotely reachable route from phone speech to this malformed service call. General host `tool` hangup capability is also distinct from the deliberately restricted `call-agent` source; the finding is not that every tool must be forbidden to stop a call.

**Smallest fix:** apply the executor's action/source and revision/idempotency acceptance rules before publishing stop intent, while preserving the serialization needed against dispatch claim. Mirror those rules in `pending_stop`; do not treat a command the executor would reject as an owner stop. Add regressions for rejected source, stale revision, and reused idempotency keys leaving no new intent. Do not simply move intent publication after enqueue without addressing the resulting race.

The supplied `test_N7_a_tool_cannot_deny_on_the_owners_behalf` checks only deny. It does not cover rejected call-agent hangup/cancel or enqueue failure after intent publication. No false “nothing dialled” statement was produced in these probes: the defect actually suppresses the call.

## Commands and results

Executed in the exact exported target using `/Users/mac/.hermes/hermes-agent/venv/bin/python`, under the sandbox above. Paths abbreviated below are absolute paths under this report directory in the actual runners.

| Command | Result |
| --- | --- |
| `python -m unittest discover -s tests/l1 -p test_r10_n7.py -v` | 7 passed, 1.771 s; `r10.log` |
| `python -m unittest discover -s tests/l1 -p test_r9_delta4.py -v` | 12 passed, 2.672 s; `r9.log` |
| `python fixtures/probe_cancel.py` | Both original startup-stop cases fixed; `cancel.log` |
| `python fixtures/probe_post_claim.py` | Stop after claim/before provider: one call then hangup, no false promise; `post_claim.log` |
| `python fixtures/probe_process.py` | Separate-process stop committed before claim: zero calls; `process.log` |
| `python fixtures/probe_rejected_source.py` | Confirms N8 rejected-source side effect; `source.log` |
| `python fixtures/probe_stale.py` | Confirms N8 stale-enqueue side effect; `stale.log` |

Runners: `run_checks.py`, `run_extra.py`, `run_process.py`, with shared `runner_prefix.py`. Only focused r10/r9 tests were rerun, not the entire L1 suite. The PATH fixture exits 97 if invoked; real ACP/provider code was not launched. All provider calls mentioned above are in-memory fixture creations.

No owner decision is needed. N7's unwanted outbound-startup dial is resolved; address N8 as validation hardening without reopening the narrow trial blocker.
