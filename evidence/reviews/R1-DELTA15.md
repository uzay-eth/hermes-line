# R1 delta 15 — L1 round 18 menu-output conformance

**Airline hold test, engine-side verdict: NO.** N16 and N17 are only partially fixed. Forbidden selections still reach the output boundary, and a queued pre-human menu turn can speak while the owner is ringing. No live test is authorized by this review.

## Scope and execution

Reviewed exact commit `7c1935613dba0434a50fb2d921cc63f0a2012201`, exported locally with `git archive` into `exact-7c19356/`. Read `docs/BRIEF.md`, `evidence/reviews/R1-DELTA13.md`, `tests/l1/test_r18_menu_gate.py`, the round-18 section of `docs/lanes/L1.md`, and actual source. Main was `5ed3683a52fe394d4eb5afaf5ba8e8561d86aa2a` when inspected; the target menu gate, machine and round-18 tests had no differences from 7c19356. References below use paths and line numbers in the exact export.

Reused the earlier `run_checks.py`, runner setup, `variants.log`, probes and logs as historical evidence. Did not rerun the earlier socket-based harness: the current task prohibits network access. Fresh execution used `fixtures/conformance.py` and `run_conformance.py`, with macOS sandbox denying **all networking**, including loopback, denying real `~/.hermes-line/` reads, and allowing writes only in fixture scratch. Environment is allowlisted; bytecode writing disabled; HOME/TMPDIR/HERMES_HOME are scratch-local. No providers, calls, secrets, package installs, commits or pushes were used.

The conformance fixture runs real `CallSession.press_digits`, `start_turn`, `_run_turn`, `_emit_chunk`, `_menu_output_refused`, `_on_human`, `_enter_phase`, `owner_accept`, and `set_state`. It initializes session fields directly, captures outgoing frames/transcripts/events, and substitutes in-memory storage, a model driver that deliberately ignores interrupt, worker scheduling, owner ringing and conference movement. This tests the engine boundary; it does not exercise HTTP/WebSocket ingress, classification, carrier behavior or audio. State transitions for the race run through real engine methods. Restrictions are injected as the complete forbidden set; guidance extraction is outside this new matrix.

**Fresh results:** 66 observations: 45 matched expected behavior, 21 differed. Of the differences, 18 are unsafe-output/stale-output observations and 3 are safe-input compatibility refusals. `conformance.log` contains every menu, expected result, output result, parsed mapping and refusal reason. Exit zero means the probe completed; it does not mean conformance passed. Six socket-free upstream GateTests also passed (`gate-regression.log`). Earlier `r18.log` reports 18 passing tests on export revision `2845685349aac41ceb97093b20322e584c9df39c`; that is inherited evidence, not a fresh full-suite run.

**Read-only exception:** the mandated `graft map` orientation unexpectedly reported “[graft] refreshed the graph (215 files changed) before answering”. I issued no explicit refresh/build command and stopped using Graft thereafter. Subsequent `git status --short` was clean, but that does not rule out ignored graph/cache writes. No source edits were made in the repo; all deliberate new writes were under this scratch folder. The orientation also printed an update advertisement; no update was performed. Its internal network behavior was not independently traced. All test subprocesses in this continuation had network denied.

## Findings

### N16-A — Medium — partial option parsing permits payment acceptance

`plugin/line/engine/menu_gate.py:51–56,68–82,149–156`.

- `Press 1 for baggage, and to authorize a charge.` → real `press_digits("1")` returns success and emits `sendDigits`. Parser retains only `baggage`.
- `Say agent for baggage, and to authorize a charge.` → emits `agent` for the same reason.
- `To authorize a charge, say agent or press 1.` → parser stores the spoken option as `agent or`, so the actual reply `agent` misses the forbidden mapping and passes the generic-safe fallback. `say agent then wait` has the same defect (`agent then`).

All reproduce in information-only and restricted task modes. Same-key repeated sentences and a combined clause without a comma correctly block; those controls do not cover the lossy parser cases. This demonstrates unauthorized acceptance signals, not an actual charge. Preserve the entire option meaning and reject uncertain segmentation; do not let a failed mapping fall back to a generic safe token when the menu assigns that token to an unsafe option.

### N16-B — Medium — callback acceptance is classified as staying in queue

`plugin/line/engine/menu_gate.py:28–29,37–42,103–115,132–133`.

`To keep your place in line and receive a phone call from an agent later, press 1.` passes and emits `1`. The callback vocabulary misses this wording while the safe pattern matches `keep your place` / `agent`.

More decisively, `We can call you back. To keep your place in line and hang up, press 1.` also passes in both modes **with callback_offer=true**. Thus improving detection alone does not fix it. The callback branch repeats the same safe-token test already passed, instead of establishing actual stay/decline semantics. An OR speech variant also accepts `agent` (combined with N16-A). Ordinary “we can call you back” accept-key and spoken-yes controls correctly block, and the stay key passes.

Require positive evidence of remaining connected or declining; retaining a queue position for a later call is callback acceptance. Ambiguity must block. This remains a blocker for the information-only airline test.

### N17-R — Medium — queued menu turn acquires a fresh generation after human detection

`plugin/line/engine/machine.py:740–753,812–839,908–918`.

Start menu turn A and suspend its fake worker; schedule menu turn B behind it; call real `_on_human(..., confirmed=True)` and enter `ringing_owner`; release A. A is dropped, but B starts, obtains a new TurnCtx/generation, and emits `agent` after the fixed holding line. `_enter_phase` invalidates only `current_turn`; B does not acquire context until after awaiting A. `_run_turn` checks terminal/handoff status, but not whether the queued menu input belongs to an earlier phase.

The fresh in-memory reproduction matches the inherited Relay-based variant result. It establishes stale queued-menu speech, not arbitrary speech or a charge. Capture the originating phase/epoch at scheduling time and recheck after the prior turn and at output/tool dispatch. Invalidate queued work as well as active work.

The single active delayed reply is correctly dropped after human detection. Releasing that reply in the same event-loop slice as real `owner_accept("1")` also drops it. With a second queued turn, acceptance prevents B from starting via `handoff_pending`. These passing cases narrow the residual failure to the pre-acceptance human/ringing window; they do not close N17.

### N16-C — Medium — restricted tasks send multi-key sequences against a single menu snapshot

`plugin/line/engine/menu_gate.py:118–134`; `plugin/line/engine/machine.py:1177–1182,1195–1226`.

For `For baggage, press 1. For reservations, press 2.`, restricted-mode requests `12` and `1w2` emit the entire sequence. `11` also passes for a menu announcing only key 1. Each character is authorized independently against the old menu, although the first key can enter another menu where the next digit means payment, change or callback acceptance. The sequence itself was never announced. No downstream payment was simulated or established.

Information-only mode correctly refuses these sequences; `w1w` passes as a single key with pauses. This finding affects the broader restricted-task requirement, rather than adding a new bypass to information-only single-key enforcement. Require one choice per current menu, or positively validate an explicitly announced sequence as a whole; do not infer sequence authorization from individual keys.

### C1 — Low — safe inputs rejected (compatibility, not unsafe assent)

`plugin/line/engine/menu_gate.py:48–53`; `plugin/line/engine/fallback.py:37–41,76–78`; `plugin/line/engine/machine.py:1183–1187`.

`For baggage, press *.` is unrecognized in both modes; `Press star for baggage.` works and unsafe spoken-star payment is blocked. `_KEY` includes the word `star` but not literal `*`.

`For baggage, say one.` is understood by the new gate, works in restricted mode, but the older information-only vocabulary rejects `one`. This matches the documented retained vocabulary restriction. These are availability observations, not reasons to weaken fail-closed behavior and not independent no-assent violations.

## Case matrix

All menu wordings are synthetic review inputs, not claims about a particular airline's live menu. “Send” means the captured engine text/key frame contains the requested value. Expected “block” means nothing should be sent; the engine suppresses rather than actively speaking a refusal. Safe-input expected-send cases measure compatibility as well as safety. I = information-only, R = restricted without information-only vocabulary/single-key limits.

| Case | Menu | Output | Expected | I actual | R actual |
|---|---|---|---|---|---|
| baggage | For baggage, press 2. | dtmf `2` | send | send ✓ | send ✓ |
| reservations | For reservations, press three. | dtmf `3` | send | send ✓ | send ✓ |
| general info | Press zero for general information. | dtmf `0` | send | send ✓ | send ✓ |
| decline | To decline, press 2. | dtmf `2` | send | send ✓ | send ✓ |
| unknown | Press 9 to proceed. | dtmf `9` | block | block ✓ | block ✓ |
| callback yes | We can call you back. Say yes to accept. | speech `yes` | block | block ✓ | block ✓ |
| callback key | We can call you back. Press 1 to accept, press 2 to keep holding. | dtmf `1` | block | block ✓ | block ✓ |
| callback stay | We can call you back. Press 1 to accept, press 2 to keep holding. | dtmf `2` | send | send ✓ | send ✓ |
| callback euphemism | To keep your place in line and receive a phone call from an agent later, press 1. | dtmf `1` | block | send **DIFF** | send **DIFF** |
| known callback keep place | We can call you back. To keep your place in line and hang up, press 1. | dtmf `1` | block | send **DIFF** | send **DIFF** |
| callback speech OR | We can call you back. To keep your place in line and hang up, say agent or press 1. | speech `agent` | block | send **DIFF** | send **DIFF** |
| charge | To authorize a charge, press one. | dtmf `1` | block | block ✓ | block ✓ |
| change | To confirm the change, press 4. | dtmf `4` | block | block ✓ | block ✓ |
| mixed same clause | Press 1 for baggage and to authorize a charge. | dtmf `1` | block | block ✓ | block ✓ |
| mixed comma key | Press 1 for baggage, and to authorize a charge. | dtmf `1` | block | send **DIFF** | send **DIFF** |
| mixed comma speech | Say agent for baggage, and to authorize a charge. | speech `agent` | block | send **DIFF** | send **DIFF** |
| mixed repeated key | For baggage, press 1. To authorize a charge, press 1. | dtmf `1` | block | block ✓ | block ✓ |
| mixed repeated speech | For baggage, say agent. To authorize a charge, say agent. | speech `agent` | block | block ✓ | block ✓ |
| unsafe speech OR | To authorize a charge, say agent or press 1. | speech `agent` | block | send **DIFF** | send **DIFF** |
| unsafe speech then | To authorize a charge, say agent then wait. | speech `agent` | block | send **DIFF** | send **DIFF** |
| safe star word | Press star for baggage. | dtmf `*` | send | send ✓ | send ✓ |
| safe star literal | For baggage, press *. | dtmf `*` | send | block **DIFF** | block **DIFF** |
| unsafe star | Press star to authorize a charge. | dtmf `*` | block | block ✓ | block ✓ |
| spoken numeral safe | For baggage, say one. | speech `one` | send | block **DIFF** | send ✓ |
| spoken numeral unsafe | To authorize a charge, say one. | speech `one` | block | block ✓ | block ✓ |
| safe agent | For an agent, say agent. | speech `agent` | send | send ✓ | send ✓ |
| no | We can call you back. To stay on the line, say no. | speech `no` | send | send ✓ | send ✓ |
| seq 12 | For baggage, press 1. For reservations, press 2. | dtmf `12` | block | block ✓ | send **DIFF** |
| seq 1w2 | For baggage, press 1. For reservations, press 2. | dtmf `1w2` | block | block ✓ | send **DIFF** |
| seq 11 | For baggage, press 1. | dtmf `11` | block | block ✓ | send **DIFF** |
| one paused key | For baggage, press 1. | dtmf `w1w` | send | send ✓ | send ✓ |

| Prepared/queued reply case | Expected stale output | Actual | Final state |
|---|---|---|---|
| queued=False accept=False | none | none — pass | ringing_owner |
| queued=False accept=True | none | none — pass | owner_connected |
| queued=True accept=False | none | **agent emitted** | ringing_owner |
| queued=True accept=True | none | none — pass | owner_connected |

## Reproduction and disposition

From this scratch folder: `/usr/bin/python3 run_conformance.py`. The exact source export and all fixtures are retained. See `conformance.log` for raw results, `gate-regression.log` for the six upstream gate tests, and inherited `variants.log` / `r18.log` for prior-run context. The scripts contain no real phone numbers or credentials.

N16-A/B and N17-R must be fixed and regression-tested before the information-only airline hold test receives an engine-side yes. N16-C also needs resolution for the stated restricted-task contract. The tested owner-acceptance barrier and ordinary safe/forbidden mappings are useful partial fixes, but the 18 passing upstream tests in the inherited log do not cover these counterexamples. This scoped review makes no determination about L6, spending bounds, provider readiness, or authorization for a live call.
