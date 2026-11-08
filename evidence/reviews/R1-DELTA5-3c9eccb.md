# R1 delta 5 security review

Reviewed local main **`98bb20270bba786677ec71f6dca76f0fec447a41`**, including `514c21a`, `89b76e2`, and `2e8bf75`, against `58a17b1`. Date: 2026-11-05.

**(a) Second outbound call-only trial to the owner's Spectrum: NO.** The original N4/N5/N6 reproductions are fixed, but the requested “no dial after cancel” property is still false through the real owner command path. Fix the Medium N7 finding below before this trial.

**(b) Attended inbound trial, Fi forwarded to the plugin line with owner ring: YES**, for the specified restricted trial: ConversationRelay, `sms_source: none`, fallback ingress without Auth Token, `hermes-line-calls` configured with only the call scope, the owner's two phones allowlisted, and the protected destination blocked. N7 concerns outbound `_dial` startup; inbound admission does not execute that path. Existing fallback limitations remain, including reliance on the path secret and REST's inability to authenticate actual Digits or the person behind caller ID. This is a security verdict for that attended trial, not permission to make a call or an assertion about uninspected live configuration.

No open Critical or High finding in this delta. N4 High is fixed. One additional Medium finding, N7, remains.

## Scope, source and execution

Read the requested plugin/test diff, L1 section 18, the r9 evidence and its generating script, and relevant actual Hermes gateway source. The attached worktree remains at `935ef62`; its graph was used only for orientation. An exact `git archive` export of the reviewed main commit was tested under this report's `disposable/` directory. Neither the checkout nor git state was changed.

Hermes gateway source: `/Users/mac/.hermes/hermes-agent`, HEAD `bf0203a794a07aac3ff764972ab9087baa4e4f4e`. References prefixed **Hermes:** are relative to that directory; all other source references are relative to the reviewed Hermes Line commit.

Execution used the installed Python/dependencies, scratch HOME/HERMES_HOME/TMPDIR, an explicitly allowlisted environment, disabled bytecode writes, and sandbox-exec. External networking and writes outside the fixture directory were denied; only authorized loopback fixture traffic was allowed. Other owner home data, including `~/.hermes-line/`, was denied. CPU, process, open-file, file-size and wall-clock limits were applied. Host loopback was used, not a separate network namespace. No actual gateway, model, provider, phone, key or live account was used. All writes are under the requested delta-5 scratch directory.

## Findings disposition

| Prior finding | Disposition | Independent result |
| --- | --- | --- |
| N4 — hidden deferred tools pass rule-12 | **FIXED** | The actual Hermes bridge-only reply now produces a refusal; hidden-tool reachability no longer makes the call admissible. |
| N5 — final destination/consent gate precedes slow startup | **FIXED for the original finding and both reproductions.** The additional cancellation claim is **not fixed**, tracked as N7. | Quiet-hours boundary and a new destination block during startup each produce zero calls. Expired original consent also refuses without reminting. |
| N6 — unchecked detector/summary session | **FIXED** | Both sessions are checked before caller text; a broader auxiliary scope or missing auxiliary checker sends no caller text. The empty editor-MCP attachment freezes toolset selection in the actual Hermes policy implementation. |

### N4: the original probe and regression coverage

`plugin/line/engine/call_tools.py:121–150` now refuses any displayed bridge tool. The `Enabled toolsets:` branch requires exactly one matching toolset per expected server and rejects every unrelated toolset. `acp_driver.py:265–272` applies the primary-session check before normal prompting; `service.py:789–810` requires successful startup before final dial authorization.

I reran the original `probe_rule12.py` using the new explicit server identity argument and reversing only the expected vulnerability assertion. The fixture registers inert tools and runs Hermes's real `assemble_tool_defs`, actual `/tools` formatter, and bridge resolution. Result:

```text
Available tools (3):
  other: tool_call, tool_describe, tool_search

guard_result: ['deferred tools behind tool_search/tool_call cannot be verified']
foreign_tool_still_in_scoped_deferred_catalog: True
```

The foreign tool can still be resolved *inside that deliberately broad fixture catalog*, as expected; the corrected admission guard refuses that catalog. No real tool side effect was executed. See `probe_rule12.log`.

`tests/l1/test_r9_delta4.py:153–178` faithfully reproduces this with the real Hermes assembler and formatter. Lines 180–194 additionally verify zero provider calls for hidden/extra scopes. `test_r8_admission.py:34–73` covers displayed call-only tools, bridge names, other identities, duplicate own-name toolsets, extra toolsets, missing identity and malformed output. These tests passed, including the Hermes-dependent test; it was not skipped.

### N5: ordering, consent and cleanup

`service.py:771–775` performs only a consent-free precheck. Startup occurs at line 791. Lines 799–807 then check the session/task and authorize the original token; lines 810–814 dispatch to the provider worker. There is no longer an ACP startup wait after authorization. The final authorization consumes consent once; there is no replacement token minted after startup. Provider I/O remains dispatched through `asyncio.to_thread`, so this is not an atomic transaction with every possible concurrent shutdown or stop request.

The original `probe_gate_gap.py` now returns:

- Quiet-hours boundary crossed during startup: refusal, **0 provider calls**.
- Destination blocked during startup: refusal, **0 provider calls**.

A separate expiry fixture advances only the consent clock by 301 seconds during startup. Result: original consent refused, **0 calls**, **1 dial token issued**, **1 consume attempt**, **0 active MCP tokens after cleanup**, driver closed. See `probe_gate_gap.log` and `probe_expiry.log`.

Cleanup follows `service.py:759–768` → `machine.py:1083–1105,1107–1130` → `service.py:574–586`. The session becomes terminal before asynchronous wrap-up, blocking tool use; wrap-up closes the driver, drops grants, revokes both token roles and removes session mappings. Cleanup is asynchronous, not completed before every refusal response, but the new tests wait for and verify it.

`test_r9_delta4.py:47–77` exercises both original timing reproductions and verifies cleanup through its helper at lines 36–45. Lines 79–98 count successful-path token issuance/consumption; lines 118–129 check that a precheck refusal starts no driver. These are meaningful tests. The cancellation test at lines 100–116 has the gap described in N7.

### N6: auxiliary checks, empty-server role and freezing

`machine.py:253–267` installs separate primary and auxiliary checks, with an empty allowed tool-name set for the latter. `service.py:854–866` attaches an independently named server with an `ask` bearer. `acp_driver.py:392–410` refuses an unchecked or rejected auxiliary session; lines 401–411 perform `/tools` before storing the admitted auxiliary session ID; caller-derived text is sent only at lines 417–419.

The adapted independent auxiliary protocol fixture records:

```text
clean scope: s1 /tools; s2 /tools; s2 caller-derived question
broader auxiliary scope: s1 /tools; s2 /tools; no question
missing auxiliary checker: s1 /tools; no auxiliary session or question
```

See `probe_auxiliary.log`. The new tests at `test_r9_delta4.py:221–264,293–343` exercise profile drift, separate server identities, check ordering and missing-check refusal. They genuinely cover the prior omission.

The bearer role is server-held, not supplied by a request parameter: `engine/call_tools.py:177–204`. Rotation is per call and role; revocation at lines 206–217 removes all roles for the call. `server/call_tools.py:76–80,88–92` binds and rechecks `(call_id, role)` and session identity. At lines 112–117, an ask bearer gets an empty tool list and cannot invoke a call tool. The actual loopback test at `test_r9_delta4.py:266–291` exercises that denial. The existing N3 delayed-body regression tests also passed after this role change.

Attaching an empty server really does freeze **toolset selection**, not necessarily every other profile setting:

1. **Hermes:** `gateway/session_local_mcp.py:233–257` includes a successfully connected server in the frozen manifest and toolsets even when its schema dictionary is empty.
2. **Hermes:** `gateway/session_policy.py:252–257` returns the stored toolsets whenever `editor_mcp_json` is non-null, bypassing profile-following toolset resolution.
3. The plugin requires that exact server to appear in the verified scope. Failed discovery that omits it does not silently pass as `No tools available.`

My `probe_scope.py` calls the actual `rebind_editor_mcp` and `LocalSessionPolicy.current_toolsets` implementations, replacing only network discovery/disconnection with an inert connected, empty-server fixture. An otherwise profile-following policy expands to `terminal` in the control case; the attached empty-server policy retains only its editor MCP toolset after the same simulated profile drift. This is a direct policy-mechanism check, not a fresh live ACP run.

## New finding N7 — Medium: an owner Hang up queued during startup still allows the outbound call

**Evidence:** `plugin/line/engine/service.py:620–639,649–656,668–671,789–814`; public slash routing at `plugin/line/engine/hermes_glue.py:414–430`. The new cancellation test is `tests/l1/test_r9_delta4.py:100–116`.

The command loop awaits the entire approval/startup/dial operation before taking the next command. During that startup, `submit('hang_up', ...)` merely queues the owner's stop request. The new pre-dispatch liveness check sees an unchanged `CALLING` task and nonterminal session because the pending stop cannot execute yet. It then creates the provider call and processes Hang up afterward.

**Concrete failure:** the owner approves a call, sees startup taking time, and uses `/line hangup <call>` before any provider request has begun. The intended cancellation is queued, but after startup succeeds the plugin places the call anyway, then hangs it up. This can ring the recipient or incur a charge after the owner tried to stop it. The approved destination remains bound; this is a revocation/timing failure, not arbitrary-number dialing.

**Independent reproduction:** `fixtures/probe_cancel.py` pauses FakeDriver startup with an event, submits the normal owner `hang_up` command, verifies zero provider calls while the command is queued, then releases startup. Result:

```text
hang_up submitted_during_startup queued provider_calls_before_resume 0
hang_up provider_calls_after_resume 1 stop_result {'ok': True} state ended
```

The fixture does not mutate task/session state to simulate cancellation. It uses the same service command path as the public slash action. No provider I/O had started when the command was queued. A secondary internal `cancel` command probe also creates a call, then is refused because `_deny` only accepts tasks still awaiting approval (`service.py:826–835`); there is no public `/line cancel` mapping, so the Hang up result is the relevant public reproduction.

**Why the new regression misses it:** `test_N5_a_call_cancelled_during_startup_dials_nothing` directly writes `TaskStatus.CANCELED` to the store in the driver-start hook. That proves the final status check works, but does not exercise how an owner can cancel. The command queue never gets a chance to set that status before dispatch.

**Smallest fix:** make an authenticated stop intent visible to the startup operation before final authorization, or run startup independently so the command executor can handle Hang up while it waits. Check that intent immediately before dispatch and clean up the provisional session. Preserve owner authentication, revision checks and idempotency. Add the event-controlled public-command reproduction above and assert **zero provider creations**, not only a terminal eventual state. Keep the original N5 policy/expiry regressions.

This is a residual gap in the r9 cancellation assurance, not a claim that r9 newly introduced the serial command queue. It blocks verdict (a); the attended inbound path does not run `_dial` startup, so it does not block verdict (b).

## Parser, identity and collision assessment

- `call_tools.py:83–100` validates and escapes the exact per-session `hl_<8 lowercase hex>` name; the appended eight-hex tag is matched syntactically. Prefixes, suffixes, another call's name, another normal profile toolset and a second matching tag are rejected by the scope form (`:134–142`). My parser probe confirmed these cases, including a duplicate exact entry. Whitespace and empty comma fields are tolerated but do not hide an unrelated nonempty field.
- Eight random hex digits provide **32 bits**, not a guaranteed globally unique identity. An accidental name collision is possible; the two-call uniqueness test proves only its sampled case. The name is not the bearer credential. **Hermes:** `session_local_mcp.py:98–108` scopes editor servers by profile/session and appends a session-derived tag; actual tool registry borrowing runs inside that scope (`:182–211`). The MCP bearer independently selects the call/role. My fixture forced the same `hl_` name into two different sessions and observed distinct scopes and tags. No cross-call authority transfer follows merely from a name collision.
- A profile MCP named `hl_<same suffix>` normally becomes `mcp-hl_...`, not the accepted `mcp-editor_hl_..._<tag>`. A deliberately similar editor-looking profile name alongside the genuine server produces an extra/nonmatching toolset or two matches and is refused. The plugin does not independently authenticate the gateway-derived tag or server provenance from this text. A malicious same-user process that can rewrite profiles, gateway policy or ACP replies remains outside this guard's trust boundary; the 32-bit name must not be promoted into an authentication secret.
- The displayed-name form accepts only allowed call names with no bridge; the auxiliary checker allows none. `Available tools (0):` would parse as empty, but the reviewed Hermes formatter emits `No tools available.` or `Enabled toolsets:` for an empty inventory (`Hermes: acp_adapter/gateway_commands.py:265–274`); those are checked separately. I found no remote path that can substitute arbitrary `/tools` text. A structured catalog/provenance API would be stronger than this version-specific textual contract, but is not needed to close the demonstrated N4 bypass.

## r9 evidence and tests run

Read `tests/l1/evidence/r9/live-default.json`, `live-hidden.json`, `live-restricted.json`, and `tests/l1/bench/scope_live_run.py`. The committed evidence reports a real ACP viewer with scratch gateway and fixture model: default and hidden-extra profiles refused with zero provider creations; the restricted profile passed primary/auxiliary scope checks, completed through the call tool and ended with zero tokens. These records support the source analysis. I did **not** rerun their profile-copy/live-gateway setup or inspect the real `hermes-line-calls` configuration.

All executions below used the exact exported target and the sandbox described above. `$PY` was `/Users/mac/.hermes/hermes-agent/venv/bin/python`; fixture script paths were absolute under this report's `fixtures/` directory.

| Command | Result |
| --- | --- |
| `$PY -m unittest discover -s tests/l1 -v` | **227 passed**, no skips, 98.675 seconds unittest time. `l1.log`. |
| `$PY fixtures/probe_rule12.py` | Prior N4 bypass refused. `probe_rule12.log`. |
| `$PY fixtures/probe_gate_gap.py` | Both original N5 cases refuse, zero calls. `probe_gate_gap.log`. |
| `$PY fixtures/probe_auxiliary.py` | Both sessions checked; drift/unchecked cases send no caller text. `probe_auxiliary.log`. |
| `$PY fixtures/probe_scope.py` | Parser negative cases, actual empty-server freezing and forced duplicate-name/session isolation pass. `probe_scope.log`. |
| `$PY fixtures/probe_cancel.py` | Confirms N7 through queued owner Hang up. `probe_cancel.log`. |
| `$PY fixtures/probe_expiry.py` | Expired consent refuses, no remint, zero calls, driver/tokens cleaned up. `probe_expiry.log`. |

The isolated PATH includes a scratch `hermes` executable that exits 97 if run; it only satisfies the driver-selection path-resolution test. Actual ACP tests use the repository's fake ACP fixture. The original probes were adapted for the new checker signature and expected fixed outcomes; the auxiliary half was split into its own script. Runners are retained as `run_suites.py`, `run_probes.py`, and `run_expiry.py`.

No new owner decision is needed. Coordinator action: fix N7 and rerun the queue-based cancellation reproduction before the outbound trial. The narrower attended inbound trial has no remaining security blocker identified in this delta under the stated configuration.
