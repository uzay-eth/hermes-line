# R1 delta 4 security review

Reviewed: `58a17b1cbf363a55213942ae52307e77545e6101`, against `1d16bfd`. Date: 2026-11-05.

**Verdict (a), second outbound call-only trial to the owner's Spectrum: NO.** Fix the incomplete tool restriction (N4, and its auxiliary-session gap N6) and move final dial authorization after agent startup (N5).

**Verdict (b), attended Fi → plugin inbound trial with owner ring: NO.** N4 affects the agent that hears an inbound caller too. Fix N4/N6 before exposing that agent to incoming speech. The forwarded-call admission, single owner-ring grant and press-1 paths themselves passed this review's checks.

These verdicts assume ConversationRelay, `sms_source: none`, no browser fill, and fallback ingress without the Auth Token. No Critical finding. One High and two Medium findings below. A separately verified clean, unchanged call profile mitigates the tool findings, but the new automatic guard does not establish that property. I did not inspect live profiles or settings.

## Scope and execution

Read the requested plugin/test delta, L1 sections 15–17, L2 round 3, relevant actual sources and tests. Used the existing graph for orientation, then verified the source. Reviewed the locally installed Hermes implementation where the plugin depends on `/tools` and ACP semantics; that repository's HEAD was `bf0203a794a07aac3ff764972ab9087baa4e4f4e`. Hermes references below are relative to `/Users/mac/.hermes/hermes-agent`; other references are relative to the reviewed Hermes Line commit.

The working checkout was older than the target. Tests used a `git archive` export of the exact target into `disposable/` under this report's directory. The checkout remained unchanged; no git writes, provider operations, external network, keys, phone calls or reads under `~/.hermes-line/` were performed.

Tests/probes used the installed Python and dependencies, an empty allowlisted environment, scratch HOME/HERMES_HOME/TMPDIR, disabled bytecode writes, and macOS sandbox-exec. The sandbox denied external network, denied writes outside the disposable fixture directory, and denied access to the owner's other home data, including `~/.hermes-line/`. Loopback fixture traffic was permitted as authorized. CPU, process, file size, open-file and wall-clock limits were applied. This was host loopback, not a separate network namespace; no real gateway or other running service was contacted. No real model was invoked.

## New findings

### N4 — High: deferred non-call tools pass the rule-12 guard

**Evidence:** `plugin/line/engine/call_tools.py:70–116`, especially the unconditional acceptance of `tool_search`, `tool_describe`, `tool_call` at lines 73 and 116; `plugin/line/engine/acp_driver.py:253–260`; `plugin/line/engine/service.py:777–791`.

The parser validates the *displayed* tool names, not the catalog accessible through the bridge. A profile with an unrelated enabled MCP tool can display only:

```text
Available tools (3):
  other: tool_call, tool_describe, tool_search
```

That passes with `[]`. The unrelated tool remains callable through `tool_call`. Handling the alternative `Enabled toolsets: ...` form does not fix this: Hermes emits the displayed-names form whenever that list is nonempty, omitting enabled toolsets from the reply.

**Concrete failure:** accidentally leave another MCP/plugin toolset in the call profile. A caller's speech can induce the call agent to discover and invoke that tool, such as an external messaging or file-access tool, despite the pre-call guard passing. The risk depends on the extra tool's authority; it is outside the phone engine's dial/OTP gates. ACP's permission callback declines requested permissions, but it is not an authorization barrier for every MCP operation. This is not a claim that a remote caller can install an MCP server.

**Reproduction:** `fixtures/probe_rule12.py` registers two inert in-memory tools, one call tool and one unrelated MCP tool. It runs the installed Hermes `assemble_tool_defs`, the actual `_tools_text` formatter, and the plugin guard. Result: the three bridge names above; guard `[]`; unrelated tool remains in the scoped deferred catalog. The real bridge resolver, supplied that fixture catalog, returns `(None, ('mcp__other__send_message', {}))`, authorizing redispatch. No external action or handler with real authority was executed. See `probe_rule12.log`.

Supporting Hermes evidence: `tools/tool_search.py:150–162,343–381` collapses registered deferrable tools into bridge schemas; `model_tools.py:523–537` applies it to returned definitions; `gateway/session_local_inventory.py:44–58,143–162` uses these definitions; `acp_adapter/gateway_commands.py:265–274` omits toolsets if names exist; `model_tools.py:716–750` resolves against the uncollapsed scoped catalog.

**Smallest effective fix:** validate the complete, uncollapsed executable catalog and exact session MCP identity, or enforce a frozen session toolset allowlist at the gateway. Until that exists, fail closed on bridge-bearing replies that do not provide a verifiable full scope. Do not infer scope from the three bridge names. The toolset-only parser should bind the exact server tag, not accept any `hermes_line_<8 hex>` identity.

**Tests:** `test_r8_admission.py:31–58` covers displayed extra names, malformed replies and the toolset-only form. Lines 72–83 and 113–146 cover fake ACP rejection before dial and success with a restricted reply. They genuinely exercise those branches, but none includes an unrelated tool hidden behind the bridge. The default-profile fixture has visible extra tools and cannot catch this bypass.

### N5 — Medium: agent startup opens a new gap after destination/consent authorization

**Evidence:** `plugin/line/engine/service.py:752–755` consumes consent and creates the permit; line 779 awaits ACP startup and the `/tools` exchange; lines 786–791 dispatch that earlier permit. `plugin/line/engine/telephony.py:63–75,88–99` checks policy when issuing the permit, then only checks permit authenticity/single use at dispatch.

**Concrete failure:** approve just before quiet hours. ACP startup crosses the boundary, then the plugin calls during quiet hours. Likewise, a destination blocked while startup is pending still gets called. The new potentially slow startup is between the final gate and the provider operation; no destination, quiet-hours or consent-expiry re-evaluation follows it.

**Reproduction:** `fixtures/probe_gate_gap.py` uses the normal service approval path and FakeAdapter. A driver-start fixture advances the clock from 20:59:59 to 21:00:01 Los Angeles: one provider call is created, approval returns success, and quiet-hours policy is now true. A second case replaces the gate settings during startup to block the destination: one call is still created while `gate.preview(destination).allowed` is false. The second case models a settings replacement, not a discovered remote settings-write path. See `probe_gate_gap.log`.

**Smallest fix:** initialize and validate the driver first, then check call liveness and perform `gate.authorize` immediately before dispatch, using the original still-valid consent token. Do not simply consume the same token twice or silently mint fresh consent after expiry. Clean up the provisional session if authorization fails.

**Tests:** `tests/l1/test_gates.py:91–97` changes time before approval; `tests/l2/test_policy_dispatch.py:39–54` changes settings before `authorize`; `tests/l1/test_r8_admission.py:248–261` blocks before request. These are useful but none changes policy/time during the new startup await. Both new reproductions succeed in violating the intended gate timing.

### N6 — Medium: the detector/summary ACP session never gets the tool check

**Evidence:** `plugin/line/engine/acp_driver.py:235–260` checks the primary session only. `ask` at lines 359–379 creates another session with `mcpServers: []` and immediately sends its question without checking tools. `plugin/line/engine/machine.py:273–278,470–474` routes detection through this path; summaries use it too.

**Concrete failure:** after the primary session passes, change the selected profile to include broader tools before the first detector/summary session is created. That second session receives the profile's tools and caller-derived text without a guard. An empty MCP attachment list does not mean an empty profile toolset. A malicious caller can then target an unchecked model session. This requires configuration drift or a broader auxiliary-session catalog; an unchanged, truly empty profile does not acquire extra tools merely because a second session exists.

**Reproduction:** the protocol fixture in `fixtures/probe_rule12.py` records all session creation and prompts. Checked sessions: `['s1']`; caller-derived question sessions: `['s2']`; no tool check for `s2`. This establishes the missing check, not live exploitation of a configured Hermes profile. Hermes `acp_adapter/gateway_server.py:269–284` creates each session from the profile without an explicit empty toolset selection; `gateway/session_policy.py:252–260` also permits profile-following toolsets where they are not frozen by an explicit selection/editor-MCP policy.

**Smallest fix:** give detector/summary sessions a frozen empty executable toolset and verify it before sending caller text. Apply the corrected full-catalog guard to every session, not just `start`; prevent later profile following from widening the scope.

**Tests:** `test_ask_uses_its_own_session` and `test_R7_ask_runs_while_a_turn_streams_and_neither_loses_text` validate separation/concurrency, not authorization. The G5 tests do not exercise auxiliary-session restrictions or profile drift.

## Requested checks that passed, and limits

### Forwarded callbacks and owner ring

- `plugin/line/server/ingress.py:237–247` keeps normal ingress authentication and a fresh fallback REST check for inbound admission. `ingress_auth.py:129–144` checks SID, account, direction, To, From and live status. `service.py:1008–1028` enforces the plugin destination, persistent one-SID admission and busy rejection. Forwarding does not open a new unauthenticated route.
- `ForwardedFrom` only affects masked log annotations (`service.py:1006,1055–1060`). `CallerName` is sanitized and labeled unverified in the briefing (`service.py:397–411`). Neither supplies a destination, task identity, consent or owner identity. Nevertheless, a name inserted into model context remains untrusted prompt content; fixing N4 matters here too.
- `service.py:992–1001,1034–1039` matches exactly one recent task by the verified record's From number and eligible status. Own/forwarding numbers produce no match. Ambiguous, absent and hidden callers remain unknown. A REST From match proves what the carrier recorded, not who physically placed the call: caller-ID spoofing can still cause a task match and the task-title greeting at `service.py:430–434`. This is an existing number-based identity limit, not a new ForwardedFrom authorization bypass.
- Unknown callers can talk to Hermes, leave a summary and request one ring of the configured owner. They cannot supply a new dial destination or owner approval through these handlers. Admission is not limited to traffic demonstrably forwarded from Fi: direct calls to the plugin number also qualify. That matches the implemented metadata-only treatment of forwarding.
- `service.py:417–428` issues one nonrenewable ring grant for an unknown inbound call. `GrantBook.take` at lines 77–80 removes it atomically. `machine.py:760–786` checks state, quiet hours, the configured owner destination and consent before ringing. Decline/no-answer clears the owner leg without restoring the grant. A fresh inbound SID can get a fresh ring grant; this is not a rate limit across calls. An owner can separately authorize another ring through an explicit control.
- The whisper uses the owner leg; `ingress.py:188–198` checks that leg, and fallback requires REST status `in-progress`. `machine.py:800–820` connects only for `Digits == '1'`; a different key declines. Caller speech is not press-1 approval. In fallback, a holder of the path secret and the relevant IDs can forge unverified Digits after the owner answers; this remains the documented fallback limitation, not proof of an actual keypress. Protecting the secret remains essential.

The seven tests in `tests/l1/test_r7_callbacks.py:48–181` passed and meaningfully exercise matching, hidden identity, unknown-caller ringing, press-1, decline, no-answer, ended-SID replay and disabling unknown-caller ringing. Several run both auth modes. They use fake provider records and scripted speech: they do not establish Fi's actual forwarding metadata, anti-spoofing or telecom delivery behavior.

### Rule-12 parsing, timing and environment

Malformed/count-mismatched text and explicit extra displayed tools fail closed. The toolset-only form rejects an explicit unrelated toolset. Five launch flags are stripped from the child environment by `acp_driver.py:59–69`; the test checks both explicit env and inherited env at Popen. The normal primary session is checked before outbound provider creation and before model use. These improvements are real, but do not cover deferred catalogs (N4), the new dial timing gap (N5), or auxiliary sessions (N6). This review did not establish an arbitrary remote path to edit the profile or process environment.

### N3: FIXED for the reported delayed-body token reuse

`plugin/line/server/call_tools.py:73–92,115–117` resolves the original bearer again after body reading and immediately before dispatch, requiring the same live session. Body reading has a streamed size check and deadline at lines 35–44.

I reran the previous split-request reproduction: authenticate headers, rotate/revoke/expire the token or end the call, then deliver the body. All four return **401**, with no finish mutation. The prior browser-shape checks also pass: unauthenticated simple POST/cookie/query forms fail, preflight is refused, and a foreign Host is refused even with a valid fixture bearer. MCP cannot directly invoke OTP or arbitrary dial tools; removing the owner's bridge grant prevents its request from dialing.

The named new N3 tests (`test_r8_admission.py:188–225`) really cover rotation, revocation, expiry, an unchanged slow request and oversize input. They synchronize with a short sleep; my reproduction waits on the first successful token resolution, removing that timing assumption. No new N3 bypass found.

### Toll-free, configured denies and browser removal

`policy/destinations.py:26,36–51,75–93` allows the seven specified toll-free NPAs, preserves strict ASCII canonical dialing and hard emergency/service/jurisdiction denies, normalizes configured block entries, and checks the blocklist before allowlist/open-mode selection. Malformed hand-built blocklists fail closed. Toll-free routing is an explicit policy exception, not evidence of physical US location or owner ownership. L2's new tests cover toll-free plus consent and malformed/normalized blocks; all passed. N5 is the remaining dispatch-timing issue above this policy layer.

The previous hard-coded protected-number deny was deliberately removed, as recorded in L2 round 3 and L1 section 17 item 4. It is now configuration dependent. This is a changed safety contract, not an unconditional deny: keep the protected number in `policy.blocked_destinations`, and keep the two owner phones as the trial allowlist. No live configuration was read or verified, and no action touched the protected number.

Browser fill is unreachable through the shipped registration/composition path: `hermes_glue.py:362–367` supplies no browser sink or origin probe and disables fill; `service.py:883–884,922–923` rejects browser requests and old pending browser approvals. `config.py:55–65` returns false and rejects attempts to enable the removed setting. Underlying SMS library code remains, but it is not wired into this build's browser tools. `hermes_glue.py:348–349` exits OTP setup for `sms_source: none`. `test_G3_browser_fill_is_not_in_this_build` passed; this is composition/registration evidence, not a fresh audit of every SMS library path.

### Silence, re-ask and hand-over

`machine.py:623–631` emits bounded fixed filler text, with generation/terminal/handoff checks; it performs no provider or tool operation. After two unclear replies, `machine.py:478–522` assumes a human and starts conversation, or in owner mode calls the existing `ring_owner` gate. A recording can trigger that assumption; it does not become owner consent. Owner requests still reach the configured number through `machine.py:641–649,760–786`. DTMF still checks menu state, format and one-send-per-generation (`machine.py:672–690`); it is not a new call leg. OTP release is not added to these paths.

The eleven tests in `test_r7_silence.py` passed. They meaningfully test response/filler timing with fake drivers, re-ask progression, recording cooldown, summary latency and bounded stop. The second-unclear-reply tests deliberately stub the detector; one separate test exercises the real detector on the recorded transcript. They do not prove model accuracy on arbitrary recordings or the tool confinement claim. The new independent question path is why N6 matters.

## Commands and results

All commands below ran in the exact exported target, with the sandbox/environment described above. `$PY` was `/Users/mac/.hermes/hermes-agent/venv/bin/python`.

| Command/check | Result |
| --- | --- |
| `$PY -m unittest discover -s tests/l1 -v` | 214 run in 95.265 s: 213 passed, 1 environment error (`hermes` absent from isolated PATH). `l1.log`. |
| `$PY -m unittest discover -s tests/l2 -v` | 74 passed in 0.068 s. `l2.log`. |
| `$PY -m unittest discover -s tests/l1 -p test_acp_and_dashboard.py -v` | 12 passed in 1.537 s with scratch PATH executable fixture; includes the previously failing driver-selection test. `l1_path_retry.log`. |
| `$PY fixtures/repro_mcp_edges_fixed.py` (absolute fixture path) | 3 passed; four delayed-body lifecycle cases all 401, no mutation. `n3.log`. |
| `$PY fixtures/probe_rule12.py` (absolute fixture path) | Confirmed N4 and unchecked auxiliary session. `probe_rule12.log`. |
| `$PY fixtures/probe_gate_gap.py` (absolute fixture path) | Confirmed both N5 timing cases. `probe_gate_gap.log`. |

The PATH fixture is a shell executable that exits 97 if invoked. The driver-selection test only resolves its path and constructs the driver; all actual ACP test conversations use `fake_acp_agent.py`. This rerun therefore resolves the isolation artifact without invoking real Hermes. Every L1 test has passed across the initial run and targeted rerun; there was not a second full L1 run.

Reproduction runners retained here: `run_suites.py`, `run_probes.py`, `run_extra.py`. The initial deferred-tool probe with unregistered fixture names did not defer them and correctly failed its assertion; the final probe registers inert schemas through the actual registry and verifies actual bridge resolution. Only the final result is relied on for N4.

No owner decision is needed to fix these findings. Coordinator: address N4/N6 tool confinement and N5 final authorization ordering, add the missing regressions, then rerun this focused review before either trial.
