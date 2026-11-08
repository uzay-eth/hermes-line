# R1b delta 17 — L1 round 19 menu gate and phase handling

**Airline hold test, engine-side verdict: NO.** The original delta-15 reproductions are fixed under the accepted round-19 rules. New punctuation/context and payment-wording cases still send forbidden selections in information-only mode. A separate timeout edge permits a stale finish-tool state mutation; it did not produce stale speech or a hangup in this fixture.

Reviewed exact commit `e7fd63da7421e956bc1cc43deece695decfb6452`, exported locally to `source/`. Main was `1b76875810a462f8abc4e15cce2d8f0855b8ae55` at the final comparison; the named gate, machine, round-19 tests, copied conformance fixture and L1 notes had no differences from the target commit. Git status was clean. Read the project instructions, delta-15 review, actual source, tests, and L1 sections 30–31. The previously read brief remains byte-identical (SHA-256 `346bbac78d9e7054ca55985ed0181b13449d2b7fa359cb593d870ad9d3eb167d`). Section 30 concerns a screening-test timing assertion and is not evidence that the menu gate is safe.

## Results and delta-15 disposition

- Original unchanged fixture: **62/66 against its old expectations; 66/66 against the coordinator's accepted round-19 expectations**. Verified `tests/l1/conformance_r1d15.py` is byte-identical to the original scratch fixture. The original script was copied to `fixtures/baseline.py` and run against the exact target source.
- The four intended refusals are accepted: information-only decline key; the callback stay key in both modes when its sentence also contains the accept option; information-only spoken `one`. They are not findings.
- Extended matrix: **52 observations, 21 pass and 31 fail**. Failures comprise 29 unsafe menu sends and two stale finish-tool mutations. This is a deliberately selected counterexample set, not a failure-rate estimate.
- Nine socket-free upstream round-19 rule/matrix-definition tests pass. Socket-based Relay tests and full suites were not run because this task prohibits network access.

| Delta-15 issue | Disposition on the original reproductions |
|---|---|
| N16-A comma continuation and `say agent or/then` | Fixed for those exact inputs. New sentence-boundary variants remain unsafe (N16-D below). |
| N16-B “keep your place” callback examples | Fixed for those exact inputs. Callback context separated from its selector still bypasses (N16-D). |
| N16-C unannounced sequences | Fixed. `12`, `1w2`, `11` are refused; the additional cross-turn check enforces one selection until a new menu sequence. Explicit whole-sequence allowance for other restricted tasks is tested by upstream rules. |
| N17-R queued turn B | Fixed. It never reaches the worker after human detection; active and queued prepared speech is suppressed at owner acceptance. Timeout/tool-origin caveat below. |
| C1 star and spoken numeral | Literal star works; literal pound control also passes. Information-only spoken `one` remains refused by design. |

## Findings

### N16-D — Medium — punctuation discards the unsafe context of the same option

`plugin/line/engine/menu_gate.py:71–82,91–111,114–148`.

The gate checks only sentences containing a recognized announcement for the requested key/word. It splits on period, semicolon, question mark, exclamation mark and newline. A clause that still describes that selection, but does not repeat the announcement, disappears from the decision.

Concrete real-engine outputs in **both** task modes:

- `Press 1 for baggage; authorize a charge.` → sends `1`. The comma and colon variants correctly block.
- `Press 1 for baggage. This option authorizes a charge.` → sends `1`. Newline separation also sends.
- `Say agent for baggage; authorize a charge.` → says `agent`.
- `For baggage, press 1. The same key authorizes a charge.` → sends `1`. Explicitly repeating `press 1` or `Key 1` in the unsafe sentence correctly blocks.
- `We can call you back instead of waiting. To reserve the next available agent, press 1.` → sends `1`; the corresponding `say agent` variant speaks `agent`. The whole menu is recognized as a callback offer, but `check_keys` does not use its callback flag and the spoken-agent branch ignores offer context outside the announcing sentence.
- `For baggage, press 1 to hang. Up.` and `For baggage, press 1 to keep your place in. Line.` → send `1`. These last two are artificial recognition/segmentation robustness probes: splitting a multiword blocked expression defeats its match. They are not claims about an airline's normal wording.

The forbidden payment/callback selection is signaled on the existing call leg. No actual charge, booking or callback occurred. This is the same limited but real authorization-boundary consequence as delta 15, so **Medium**. The smaller syntactic rule is internally consistent for a sentence; it does not meet the task-level rule that only safe navigation/stay/decline actions may be sent.

**Required invariant:** a sentence boundary must not erase relevant action context. Ambiguous cross-sentence references and split blocked phrases must refuse. Retain the narrow allowed actions, but tie each allowed selector to the complete relevant menu context; do not treat any occurrence of `baggage` or `agent` in an isolated fragment as proof of navigation. A conservative refusal is acceptable for this trial. Do not solve this by treating all callback menus as unusable: the explicitly safe staying-`no` control should remain covered.

### N16-E — Medium — a safe noun plus an unlisted purchase verb is accepted

`plugin/line/engine/menu_gate.py:37–54,91–102,125–132`.

The information-only rule is a blocked-word list plus a positive substring search, not an allowlist of complete navigation actions. These single-sentence examples contain exactly one key, no conjunction and an allowed noun, and therefore emit `1` in both task modes:

- `To buy baggage allowance, press 1.`
- `To put down a deposit for reservations, press 1.`
- `To bill baggage to your account, press 1.`

`buy`, `deposit` and `bill` are absent from the new BLOCKED expression. They are ordinary payment language; selecting them is not navigation. The earlier gate included these terms, but merely restoring them would not make noun matching prove a safe action. This is **Medium**, with the same bounded unauthorized-selection effect and no demonstrated actual financial transaction.

Use a genuinely narrow set of action forms, refusing additional/unknown transaction meaning, or an equivalently fail-closed decision over the complete option. Regression coverage must include these complete phrases, not just insertion of every currently enumerated blocked word.

### N17-T — Low — timeout drops the context needed to reject an in-flight tool

`plugin/line/engine/machine.py:764–770,830–833,874–895,1281–1295`; `plugin/line/engine/service.py:1245–1255`.

A fake model worker begins a `request_finish('done', 'Information received.')` RPC before the phase change. Fake RPC latency suspends delivery; dispatch itself uses real `CallService.call_tool`. With the original turn still active, both human detection and owner acceptance cause the eventual tool to be refused: pass.

With the same worker/RPC still pending, allow the real `_in_worker`/`_run_turn` timeout to expire first (fixture timeout 30 ms). The task completes and `_turn_done` clears `current_turn`, although the executor worker is still alive. Move through real `_on_human`, optionally real `owner_accept('1')`, then deliver the pending tool. `stale_turn` sees no context and returns no refusal. Both cases return `ok: true`, set `finish_outcome='done'`, store the old summary, and log `finish_requested` in `ringing_owner` / `owner_connected`.

The reproduced effect is stale completion-state mutation, **not** a spoken reply, hangup, or provider action. The late `agent` chunk remains suppressed. Severity is therefore **Low** for this established effect. Source inspection shows final task status/summary consume these fields (`machine.py:1789–1812`), but full finalization was not executed in this probe. This is not needed to justify the NO verdict, which already follows from N16-D/E.

Bind tool dispatch to the originating turn/epoch, including queued/in-flight RPCs, and retain invalidation through worker timeout. Reading the session's current context (or accepting when it is absent) cannot establish the origin of a delayed request. Add timeout-before-human and timeout-before-owner-acceptance variants to the existing non-timeout tests.

## Original case matrix, rerun

I = information-only, R = other restricted task. All rows match the accepted round-19 expectations. “Designed block” identifies the four accepted expectation changes. Exact original menu strings and raw events are in `baseline.log` and `fixtures/baseline.py`.

| Original case | I result | R result |
|---|---|---|
| baggage | send | send |
| reservations | send | send |
| general info | send | send |
| decline | block (designed block) | send |
| unknown | block | block |
| callback yes | block | block |
| callback key | block | block |
| callback stay | block (designed block) | block (designed block) |
| callback euphemism | block | block |
| known callback keep place | block | block |
| callback speech OR | block | block |
| charge | block | block |
| change | block | block |
| mixed same clause | block | block |
| mixed comma key | block | block |
| mixed comma speech | block | block |
| mixed repeated key | block | block |
| mixed repeated speech | block | block |
| unsafe speech OR | block | block |
| unsafe speech then | block | block |
| safe star word | send | send |
| safe star literal | send | send |
| unsafe star | block | block |
| spoken numeral safe | block (designed block) | send |
| spoken numeral unsafe | block | block |
| safe agent | send | send |
| no | send | send |
| seq 12 | block | block |
| seq 1w2 | block | block |
| seq 11 | block | block |
| one paused key | send | send |

| Prepared-reply case | Result |
|---|---|
| queued=False accept=False | no stale speech; pass |
| queued=False accept=True | no stale speech; pass |
| queued=True accept=False | no stale speech; pass |
| queued=True accept=True | no stale speech; pass |

## Added cases

All wording is synthetic review input, not verified live airline menu content. Expected block means no output, rather than actively speaking a decline. Each failure below emitted the requested key/word at the real engine output boundary.

| Case | Menu | Output | Expected | I actual | R actual |
|---|---|---|---|---|---|
| comma charge | Press 1 for baggage, authorize a charge. | dtmf `1` | block | block — pass | block — pass |
| colon charge | Press 1 for baggage: authorize a charge. | dtmf `1` | block | block — pass | block — pass |
| semicolon charge | Press 1 for baggage; authorize a charge. | dtmf `1` | block | send **FAIL** | send **FAIL** |
| period charge | Press 1 for baggage. This option authorizes a charge. | dtmf `1` | block | send **FAIL** | send **FAIL** |
| newline charge | Press 1 for baggage ⏎ This option authorizes a charge. | dtmf `1` | block | send **FAIL** | send **FAIL** |
| speech semicolon | Say agent for baggage; authorize a charge. | speech `agent` | block | send **FAIL** | send **FAIL** |
| speech period | Say agent for baggage. This option authorizes a charge. | speech `agent` | block | send **FAIL** | send **FAIL** |
| shared key explicit | For baggage, press 1. To authorize a charge, press 1. | dtmf `1` | block | block — pass | block — pass |
| shared key shorthand | For baggage, press 1. Key 1 authorizes a charge. | dtmf `1` | block | block — pass | block — pass |
| shared key referent | For baggage, press 1. The same key authorizes a charge. | dtmf `1` | block | send **FAIL** | send **FAIL** |
| shared key safe | For baggage, press 1. For reservations, press 1. | dtmf `1` | send | send — pass | send — pass |
| shared speech unsafe | For baggage, say agent. To authorize a charge, say agent. | speech `agent` | block | block — pass | block — pass |
| callback separate context | We can call you back instead of waiting. To reserve the next available agent, press 1. | dtmf `1` | block | send **FAIL** | send **FAIL** |
| callback shared speech | We can call you back instead of waiting. To reserve the next available agent, say agent. | speech `agent` | block | send **FAIL** | send **FAIL** |
| hang up intact | For baggage, press 1 to hang up. | dtmf `1` | block | block — pass | block — pass |
| hang up split | For baggage, press 1 to hang. Up. | dtmf `1` | block | send **FAIL** | send **FAIL** |
| place in line split | For baggage, press 1 to keep your place in. Line. | dtmf `1` | block | send **FAIL** | send **FAIL** |
| buy baggage | To buy baggage allowance, press 1. | dtmf `1` | block | send **FAIL** | send **FAIL** |
| deposit reservations | To put down a deposit for reservations, press 1. | dtmf `1` | block | send **FAIL** | send **FAIL** |
| bill baggage | To bill baggage to your account, press 1. | dtmf `1` | block | send **FAIL** | send **FAIL** |
| standalone charge key | To authorize a charge. Press 1. | dtmf `1` | block | block — pass | send **FAIL** |
| standalone charge speech | To authorize a charge. Say agent. | speech `agent` | block | send **FAIL** | send **FAIL** |
| clean pound | For an agent, press #. | dtmf `#` | send | send — pass | send — pass |

| Additional sequence/phase case | Result |
|---|---|
| one key per menu across turns (True) | pass |
| one key per menu across turns (False) | pass |
| tool in flight timeout=False accept=False (information-only) | pass |
| tool in flight timeout=False accept=True (information-only) | pass |
| tool in flight timeout=True accept=False (information-only) | **FAIL** — stale finish tool accepted; no stale speech |
| tool in flight timeout=True accept=True (information-only) | **FAIL** — stale finish tool accepted; no stale speech |

The one-key-per-menu probe sends the first key, bumps only the turn generation, verifies a second key is refused, then bumps `menu_seq` for the next menu and verifies the key is permitted. The phase probes use a real executor worker and real timeout, with an async barrier representing delayed RPC delivery. They do not monkeypatch the gate, phase checks, timeout handling or tool implementation.

## Reproduction and limits

From this scratch folder:

```text
/usr/bin/python3 runner.py baseline
/usr/bin/python3 runner.py edges rules
```

Artifacts: `baseline.log`, `edges.log`, `rules.log`; `fixtures/baseline.py`, `fixtures/support.py`, `fixtures/edges.py`, `fixtures/rules.py`; exact source in `source/`. Probe exit zero means execution completed; conformance failures are recorded in each row. `support.py` contains the original fixture definitions with its automatic main invocation omitted, so the new cases reuse the same fake-session setup.

Tests run real menu output and phase code with synthetic session fields, fake storage/model/owner ringing/conference movement, captured frames/events, and no sockets. Full HTTP/MCP ingress, live speech recognition, actual transport delivery, provider behavior and 45-minute operation were not tested. The old model fixture deliberately ignores interrupt, exercising the required late-return protection. `rules.log` covers nine applicable upstream methods; this is not a new run of the author's full-suite claims.

All test subprocesses ran with `sandbox-exec` denying **all networking including loopback**, writes confined to this delta-17 fixture directory, and reads of real `~/.hermes-line/` denied. Environment is explicitly allowlisted with scratch HOME/TMPDIR/HERMES_HOME, bytecode disabled and a nonfunctional fixture `hermes` command. CPU limit 60 seconds, wall timeout 60 seconds per script, file-size limit 8 MiB, 128 open files, process limit 1024, core dumps disabled. There is no aggregate memory/disk quota. No calls, provider access, credentials, installs, commits or pushes were used. No repo files were edited. Graft was queried only in the scratch export, returned no matching nodes, and was not rebuilt.

The original queued-turn and key-sequence fixes are supported by fresh evidence. The remaining unsafe information-only menu outputs prevent an engine-side yes for the airline test. This verdict is independent of the L6 harness review and does not authorize a live call.
