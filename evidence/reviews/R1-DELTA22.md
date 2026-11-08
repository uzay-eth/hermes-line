# R1b delta 22 — silence first (L1 round 23)

**Combined Delta hold-test verdict (engine and harness): NO.** N16-I’s reported cases are fixed, including the intended silence on “Please select an option.” A remaining whole-menu conformance gap permits a safe department selection when another option offers an unrecognized callback. The reproduced output does not select the callback option; it nevertheless violates the coordinator’s rule that any callback offer silences the entire menu. No new harness finding was established.

Reviewed main export `e44b38a205329f90da11cbde5aa74a9a906ce00e`, containing L1 `005de0fa2e9fe6ff9180f04502ae8d3a723690bd`. Scoped engine/r23 files have no diff from that commit. Trial code and L6 tests have no diff from previously reviewed `b137b1b`. Read the change, output hook, r23 tests and L1 section 36 against `evidence/reviews/R1-DELTA21.md`, which matches the delivered delta-21 report. The brief’s required hash is unchanged. Main was clean before and after the review. Source references below are relative to the exported tree.

The only file read under `~/.hermes-line/` was the permitted `approved_destinations.json`. The Delta row still has `intro_names: ["Delta", "Delta Air Lines"]`. Those names were used in the real-session fixtures; no approval-file edits or other protected-directory reads occurred.

## N16-J — Low — an unrecognized callback option does not silence mapped safe outputs

**Source:** `plugin/line/engine/menu_gate.py:255–281,317–351,359–404`; `plugin/line/engine/machine.py:1196–1218`. The deterministic classification involved is in `plugin/line/detect/__init__.py:135–138`.

These menus were sent through the actual classifier and `_on_detection()` dispatch. Speech uses the real buffered menu-turn output hook with a fake model; keypad tests dispatch the menu with a silent fake response and then invoke real `press_digits()`.

| Menu | Requested output | Information-only | Other restricted |
|---|---|---|---|
| For reservations, press 1. To have an agent phone you instead, press 2. | key 1 | **sent** | **sent** |
| For reservations, say agent. To have an agent phone you instead, press 2. | agent | **spoken** | **spoken** |
| For reservations, press 1. To be contacted when an agent is free, press 2. | key 1 | **sent** | **sent** |
| For reservations, say agent. To be contacted when an agent is free, press 2. | agent | **spoken** | **spoken** |

Expected under the coordinator’s whole-menu rule: no output in every row. The first wording explicitly offers a phone callback; the second is a related contact-offer variant. These are synthetic cases, not claims about Delta’s actual recording.

The classifier returns `ivr/high` without callback evidence. Both menus parse into separate choices. The whole-menu lexical checks miss “phone you” and “be contacted”; they match forms such as “phone call” and “contact you” instead. As a result, `menu_problem()` returns no reason to silence. `check_keys()` validates only announcements of key 1; announced speech validates only announcements of `agent`. The new requirement that **every** option pass `positive()` is applied only to *unannounced generic* speech at lines 399–403, so it does not protect these two paths.

The new hook does run the whole-menu check first. This is incomplete coverage in that check, not an ordering bypass. Captured effects are `{"type":"sendDigits","digits":"1"}` or `{"type":"text","token":"agent","last":true}`, with no `restricted_action_blocked` event. There are eight failing observations across the two wordings, two paths and two modes.

**Severity and scope:** Low, as a whole-menu policy/conformance gap. The emitted choices are positively mapped to reservations, and the tests do **not** demonstrate callback acceptance, an unauthorized paid action, or a remote callback being created. The gap still prevents a YES under the explicit all-menu silence decision. It is a residual coverage issue, not claimed to be newly introduced by 005de0f.

Controls clarify the boundary:

- Generic, unannounced `agent` is refused on both mixed menus, in both modes: the new every-option check works on that path.
- Replacing the callback wording with “To have an agent **call you back**, press 2” silences all tested paths in both modes.
- Selecting key 2 itself is refused. Reusing key 1 or spoken `agent` for both the safe and callback options is also refused. These are **12/12** passing additional controls.
- The documented harmless auxiliary “To repeat, press 9” still permits mapped reservations key/speech and refuses generic speech. This is recorded as expected behavior, not a finding.

**Correction:** a whole-menu decision should not infer that an unselected, unrecognized option is harmless merely because the requested output has a positive mapping. Validate the other options’ meanings before permitting any output, or fail closed on unknown options; explicitly recognize any harmless auxiliary wording that must remain usable. Apply that decision equally to keypad, announced speech and generic speech. Adding these two paraphrases is a regression guard, but the structural distinction between mapped outputs and generic speech would otherwise remain.

## N16-I and the silence-first change

The earlier deterministic-classifier reproduction, “We can ring you back. Select an option.”, is silent for `agent` and `representative` in both modes. All 12 earlier direct callback-without-choice failures also now refuse. Unknown no-option text is no longer accepted as a valid empty menu: `_parse()` validates every pending sentence as a known intro/hold line.

Both prior matrices were rerun unchanged and with the **single coordinator-approved expectation adjustment** for “Please select an option.”:

| Fixture | Raw earlier expectation matches | Current-policy result |
|---|---|---|
| `callback_no_options.py` | 12/16; only the four generic select-an-option controls differ | `callback_no_options_r23.py`: **16/16** |
| `approved_edges.py` | 122/124; only the two generic select-an-option controls differ | `approved_edges_r23.py`: **124/124** |

The adjustment changes expectations only, not menus, fake responses or session state. The original copies are retained to make that distinction reviewable.

The new first check is present in `CallSession._menu_output_refused()` and also in both pure output validators. A scratch adaptation of the upstream eight silence reasons and ten output paths exercises the actual output hook and real key/menu-speech emission methods in both modes: **160/160 pass**. It verifies a whole-menu “nothing is sent” reason and no frames for keys 1/2, sequence 12, star, agent, representative, yes, no, one and operator. Reasons cover detector callback evidence, callback paraphrases, payment, compact amount, unknown leading text and trailing text.

New hold/wait and generic-speech edges are **60/60**: known hold sentences, approved Delta intros and numeric/spoken wait estimates work; currency-bearing wait estimates, payment suffixes, callback additions, punctuation-loss additions and unknown hold extensions refuse. A hold line after an option remains refused. A generic request works when all options are positive and refuses when an unrecognized repeat option is present. These checks found no separate problem in the hold-line or wait-duration exemptions.

## Earlier matrices and harness regression checks

| Fixture / suite | Fresh result |
|---|---|
| Delta-20 matrix with approved Delta wording | **132/132**, including all 36 original failing observations now refusing |
| Delta-19 matrix with approved Delta wording | **10/10** |
| Delta-17 extended cases | **52/52** |
| Original delta-20 Sample Air wording | 122/132 old expectations; ten intended unapproved-name refusals |
| Original delta-19 Sample Air wording | 8/10 old expectations; two intended unapproved-name refusals |
| Original delta-15 matrix reused in earlier reviews | 59/66 old expectations; seven previously accepted stricter refusals |
| Small-gate rules / matrix consistency | 9 tests, OK |
| Whole-menu rules | 5 tests, OK |
| Leading-text rules | 7 tests, OK |
| Approved-name socket-free rules | 2 tests, OK |
| R23 generic-speech and reviewer-fixture tests | 5 tests, OK |
| L6 selected socket-free SafetyTests/HoldTests | **38 tests, OK** |
| SID-less automatic-recovery collision probe | **1 test, OK** |
| Approval → task options → stored/session options fixture | **18/18** |
| PIN assertion plants/controls | **12/12** expected outcomes |

Delta-17’s shared-key/speech, punctuation, split-phrase, callback, payment-verb and one-key-per-menu cases pass. Its four in-flight tool cases pass with timeout on/off and owner acceptance on/off: no stale finish or speech. The original four active/queued prepared-reply cases also remain suppressed across human arrival/owner acceptance.

The seven old baseline differences remain the accepted decline refusals in both modes, callback-staying key refusals in both modes, information-only spoken `one`, and staying `no` refusals in both modes. They are not regressions.

The harness and approval-name path are unchanged from delta 21. The fake-boundary integration again shows that names come only from the approval row, are validated, displayed, stored and loaded into the session. Missing/null/empty names leave the Delta greeting unapproved even when the title names Delta. The real service is never started, no approve/dial command is issued, and the fixture asserts no adapter invocation. Recovery/hold tests retain startup blocking, unresolved-create journaling, no automatic SID-less clear, coordinator reconciliation, stop/dispatch locking, draining and late-SID hang-up behavior. The collision probe retains its marker and does not declare a different terminal call proof of recovery. No new harness finding was established.

PIN payload plants remain detectable; the previously documented nested timestamp/ID-field exclusions are unchanged and are not a new finding. No live PIN or credential was accessed.

## Artifacts and execution limits

[CASE-RESULTS.md](CASE-RESULTS.md) lists every matrix/integration/PIN observation. `mixed_callback.log` contains the eight N16-J failures and 16 passing controls; `mixed_callback_controls.log` contains the 12 additional refusal controls. `silence_paths.log` and `hold_edges.log` contain the fresh round-23 edge checks. Exact menus, detector evidence, events and frames remain in the logs and scripts under `fixtures/`.

Reproduce from this scratch folder:

```text
/usr/bin/python3 runner.py baseline edges extra intro_edges extra_delta intro_edges_delta rules r20_rules r21_rules r22_rules r23_rules l6_selected l6_residual pin_plants approved_edges callback_no_options approved_edges_r23 callback_no_options_r23 task_flow silence_paths hold_edges mixed_callback mixed_callback_controls
```

Observation scripts return zero when execution completes; inspect `pass_case` for conformance. The upstream Relay/socket suites and full dry rehearsal were not run because networking, including loopback, is denied. No full-suite pass is claimed. Tests exercise real parser/output, deterministic detection/dispatch, turn tracking and task/storage code with fake model/transport/provider boundaries.

All test subprocesses used `sandbox-exec`: networking denied, real `~/.hermes-line/` reads denied, writes confined to delta-22 fixtures, source/toolchain read-only. The permitted approval-file read was separate and printed only label/name fields. The environment is allowlisted with scratch HOME/TMPDIR/HERMES_HOME, bytecode disabled and a nonfunctional `hermes` stub. Limits: 60 CPU seconds and 60 seconds wall time per script, 8 MiB per file, 128 open files, process limit 1024, no core dumps; no aggregate memory/disk quota. The export contains no tracked Graft index; source/diffs were used without rebuilding or updating a graph.

No network, calls, provider access, real credentials, other `~/.hermes-line/` reads, installs, repository edits, commits or pushes occurred. Live Delta menu/STT behavior, remote provider behavior and spending readiness remain unverified. The combined NO rests on N16-J’s violation of the whole-menu callback-silence rule, despite the passing requested reproductions and harness regressions.
