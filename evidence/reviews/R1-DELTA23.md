# R1b delta 23 — fully positive menu rule (L1 round 24)

**Combined Delta hold-test verdict (engine and harness): YES.** The reviewed code conforms in the requested reproductions and added edge cases, with the approved intro names **Delta** and **Delta Air Lines** and the coordinator’s accepted fail-closed behavior. N16-J is closed. No new finding was established.

Reviewed main export `73135efbe11d9a5be6a8d01d5407b9237b6e82d2`, including L1 `352649a`. The scoped engine and r24 test files have no diff from that commit. Trial code and L6 tests remain unchanged from reviewed `b137b1b`. Read the change, actual parser/checking code, `tests/l1/test_r24_fully_positive.py`, copied reviewer fixtures and L1 section 37 against `evidence/reviews/R1-DELTA22.md`; the earlier report is byte-identical to the delivered scratch artifact. The required brief hash is unchanged. Main was clean before and after the review.

The only file read under `~/.hermes-line/` was the permitted `approved_destinations.json`. The Delta row still contains exactly `intro_names: ["Delta", "Delta Air Lines"]`. These names are set in the test sessions and carried through the task-option integration fixture. The file was not modified.

## Findings and closure

**New findings: none. Severity: not applicable.** This is an offline code/fake-boundary conformance verdict, not a claim that the live Delta recording or provider behavior was exercised.

**N16-J (previously Low): closed.** `menu_gate.menu_problem()` now iterates over every parsed option and requires `positive()` before returning success (`plugin/line/engine/menu_gate.py:337–348`). The existing parser checks all remaining text as known intros/hold lines or refuses it. The common machine output hook invokes this whole-menu check before selecting the keypad or speech path. Thus a positive mapping for the requested choice can no longer conceal an unknown alternative option.

All eight original failures now produce no output:

| Other option alongside a reservations key/spoken choice | Requested output | Information-only | Other restricted |
|---|---|---|---|
| To have an agent phone you instead, press 2. | key 1 | silent | silent |
| To have an agent phone you instead, press 2. | agent | silent | silent |
| To be contacted when an agent is free, press 2. | key 1 | silent | silent |
| To be contacted when an agent is free, press 2. | agent | silent | silent |

These reruns retain the actual deterministic classifier and `_on_detection()` dispatch, real buffered speech/key output handling, fake model and captured transport frames. The associated callback-choice/shared-key/shared-word controls remain **12/12** passing.

The raw delta-22 mixed-menu script is **20/24** against its old expectations: only the four previously allowed “To repeat, press 9” controls now refuse. In `mixed_callback_r24.py`, changing only that expectation to silence yields **24/24**. The refusal is the expressly accepted cost, not a regression. The added callback words are useful regression guards, but the structural fix no longer depends on recognizing those words.

## Independent checks of the positive rule

`positive_edges.py` provides **692/692** passing observations:

| Check | Count | Result |
|---|---:|---|
| Six unknown forms before, between or after safe choices; nine output paths; both modes; normal denylists | 324 | Whole-menu refusal; no frames |
| Same matrix with BLOCKED and CALLBACK_WORDING replaced by nonmatching patterns in process | 324 | Same refusal; demonstrates positive validation independently of both denylists |
| Unknown option separated by comma, colon, semicolon, period, newline, exclamation or question mark; key and generic speech; both modes | 28 | Silent |
| Fully allowed Delta intro/hold/options, announced/generic agent, word key, star, pound and wait-estimate controls | 14 | Allowed output sent |
| Repeated turns on an unusable menu, followed by a new safe menu | 2 | Remains silent until safe replacement; then sends its allowed key |

The six unknown forms are an unlisted department, repeat instruction, return-conversation option, bare `say operator`, an extra qualifier on baggage, and a standalone unrecognized sentence. Tested outputs are keys 1/2/12/star/pound and speech agent/representative/yes/no. All refused cases also return the whole-menu “nothing is sent” reason. Patches are confined to the test process; no source files were modified.

The existing **160/160** silence-path checks and **60/60** hold/wait checks also pass. Known hold lines and exact numeric/spoken wait estimates remain usable; currency-bearing estimates, payment/unknown suffixes and callback additions refuse. Intros/hold lines after an option remain refused by the existing parser. This is stricter than the general positive-rule wording and is documented as intentional in section 37; it creates silence rather than an unauthorized send.

## Requested earlier-case reruns

| Fixture | Fresh result |
|---|---|
| Delta-22 mixed callbacks, adapted only for accepted repeat refusal | **24/24** |
| Delta-22 callback-choice/shared-key/shared-word controls | **12/12** |
| Delta-22 silence paths | **160/160** |
| Delta-22 hold edges | **60/60** |
| Delta-21 approved edges with accepted select-an-option refusal | **124/124** |
| Delta-21 real detection/dispatch cases with accepted select-an-option refusal | **16/16** |
| Delta-20 matrix with approved Delta wording | **132/132** |
| Delta-19 matrix with approved Delta wording | **10/10** |
| Delta-17 extended matrix | **52/52** |
| Original delta-15 matrix | 59/66 old expectations; seven previously accepted stricter refusals |

Original unadapted scripts were also rerun and retained:

- Delta-21 `approved_edges.py`: 122/124; its only two differences are refusal of “Please select an option.”
- Delta-21 `callback_no_options.py`: 12/16; its only four differences are the same accepted refusal.
- Original delta-20 Sample Air wording: 122/132; ten controls name an unapproved business and therefore refuse.
- Original delta-19 Sample Air wording: 8/10; the two unapproved-name greeting controls refuse.
- The seven original baseline differences remain decline in both modes, callback-staying keys in both modes, information-only spoken `one`, and staying `no` in both modes. All are refusals, not unsafe sends.

The Delta variants replace Sample Air with Delta Air Lines, and Sample with Delta, as documented in earlier reviews. The approved names remain exactly the two live-run names; Sample Air was not added to that list. No earlier scratch files were changed.

The prior stale-turn checks still pass: four active/queued replies are suppressed across human arrival/owner acceptance; four in-flight tool cases pass with timeout on/off and acceptance on/off, without recording a stale finish or emitting stale speech. One-key-per-current-menu checks pass in both modes. Earlier punctuation/shared-key, split-phrase, callback, payment-verb, compact-amount, intro-name and star/sequence cases remain covered by the rerun matrices.

## Harness and supporting tests

No harness code changed. The selected L6 socket-free suite passes **38 tests**, and the automatic-recovery different-call collision probe passes **1 test**. The reviewed controls retain unresolved-create journaling/startup blocking, no automatic SID-less clearing, explicit coordinator reconciliation, stop/dispatch locking, draining of in-flight creates and hang-up of a late-returning SID. The collision probe retains the marker rather than treating another terminal call as proof that the unresolved attempt ended. No new harness finding was established.

The approval/task integration fixture is **18/18**: real synthetic-row approval parsing, real L6 `task_args()`, real `request_call()`, scratch SQLite persistence and the CallSession constructor carry the explicit names and show them in the approval note. Missing/null/empty names leave a Delta greeting unapproved even when the label names Delta. Invalid names are rejected. The service is never started, no approve/dial command is issued, and the fixture asserts no provider-adapter invocation.

| Socket-free upstream tests | Result |
|---|---|
| Small-gate rules / matrix consistency | 9, OK |
| Whole-menu rules | 5, OK |
| Leading-text rules | 7, OK |
| Approved-name rules | 2, OK |
| Silence-first rules / reviewer fixtures | 5, OK |
| Fully positive rules / reviewer fixtures | 5, OK |
| L6 selected recovery/hold suite | 38, OK |
| Recovery collision probe | 1, OK |

That is **72 passing unittest tests**, in addition to the observation matrices. The PIN helper’s **12/12** expected outcomes also remain unchanged: nine ordinary payload plants detected, collision control excluded, and the two previously documented nested metadata-key exclusions retained. No new PIN issue or production PIN leak was established.

## Artifacts, reproduction and scope

[CASE-RESULTS.md](CASE-RESULTS.md) lists every matrix/integration/PIN observation. Exact synthetic menu strings, expectations, outputs, detector evidence and events are retained in the corresponding `.log` files and scripts under `fixtures/`. Observation scripts return zero when execution completes; inspect `pass_case` for conformance. The raw older-expectation mismatches above are all documented refusals. No unexplained unsafe output remains in the executed cases.

Reproduce from this scratch folder:

```text
/usr/bin/python3 runner.py baseline edges extra intro_edges extra_delta intro_edges_delta rules r20_rules r21_rules r22_rules r23_rules r24_rules l6_selected l6_residual pin_plants approved_edges callback_no_options approved_edges_r23 callback_no_options_r23 task_flow silence_paths hold_edges mixed_callback mixed_callback_r24 mixed_callback_controls positive_edges
```

All test subprocesses ran under `sandbox-exec`, denying all networking including loopback and real `~/.hermes-line/` reads. Source/toolchain were read-only to tests; writes were confined to delta-23 fixtures. The separately permitted approval-file read printed only label/name fields. The environment is allowlisted with scratch HOME/TMPDIR/HERMES_HOME, disabled bytecode and a nonfunctional `hermes` stub. Limits: 60 CPU seconds and 60 seconds wall time per script, 8 MiB per file, 128 open files, process limit 1024, no core dumps; no aggregate memory/disk quota. No tracked Graft index was present in the export; source/diffs were used without rebuilding or updating a graph.

The full Relay/socket suites and live/dry socket rehearsal were not run under the no-network restriction. No full-suite or live-call success is claimed. Real Delta wording/STT, provider behavior and spending readiness remain unverified. Unknown real menus may yield silence by design, including harmless unlisted options; that limitation is expressly accepted for this verdict.

No network, calls, provider access, real credentials, other `~/.hermes-line/` reads, package installs, repository edits, commits or pushes occurred. All review artifacts were written only under the requested delta-23 scratch folder.
