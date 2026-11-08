# R1b delta 21 — approved intro names (L1 round 22, L6 round 14)

**Combined Delta hold-test verdict (engine and harness): NO.** The requested fixes close all 36 delta-20 reproductions, and the approved-name path works. A remaining engine case speaks during a callback offer when the menu contains no recognized key/word announcement. This violates the coordinator’s whole-menu silence rule. No new harness finding was established.

Reviewed main export `5544fb53ebd241a481cceb0ecb8f7090f50207d2`, containing L1 `b32ca116cd54fbe52ea6347ae01c83075954aeb3` and L6 `b137b1b44e5fb125ee88892b0b2f1043b0557fe3`. The scoped engine/r22 files have no diff from b32ca11; trial code/tests have no diff from b137b1b. Read L1 section 35, L6 round 14 and the earlier review; the repository `evidence/reviews/R1-DELTA20.md` is byte-identical to the delivered scratch report. Main was clean before and after this review. Source references below are relative to the exported tree.

Read only `~/.hermes-line/approved_destinations.json` under that directory. The Delta reservations row contains **`["Delta", "Delta Air Lines"]`**. Those exact names were used throughout the earlier-case reruns and new output probes. The approval file was not changed, and its other fields were not copied into the test fixtures.

## N16-I — Medium — a callback menu with no recognized announcement permits generic speech

**Source:** `plugin/line/engine/menu_gate.py:217–241,278–304,335–352`; `plugin/line/detect/__init__.py:135–138`; `plugin/line/engine/machine.py:669–697,1196–1216`.

Reproduction, using the actual deterministic classifier, actual `_on_detection` dispatch, real turn/output gate and a fake model returning the requested word:

> We can ring you back. Select an option.

| Mode | Model reply | Required by whole-menu callback rule | Actual |
|---|---|---|---|
| Information-only | agent | silence | **spoken** |
| Information-only | representative | silence | **spoken** |
| Other restricted | agent | silence | **spoken** |
| Other restricted | representative | silence | **spoken** |

The classifier returns `ivr/high` because of “select an option,” with the menu text as evidence and no callback flag. This is the real deterministic result; the reproduction does not forge an IVR label or suppress a returned callback flag. `_on_detection()` records the menu and invokes the fake response through the normal menu-turn path. The output includes `{"type":"text","token":"agent","last":true}` (or `representative`), with no `restricted_action_blocked` event.

Why it passes:

1. `announcements()` recognizes neither “ring you back” nor “select an option” as a concrete key/word choice.
2. With no announcement, `_parse()` returns an empty option list plus the complete text. It never applies `_intro_ok()` to that pending text. This is a valid parse rather than a refusal.
3. The remaining negative checks do not recognize “ring you back” as callback wording. `menu_problem()` returns no problem.
4. `check_speech()` permits unannounced `agent` and `representative` as generic requests.

The approved name list does not close this path because the menu has no name or recognized option. The wording is a synthetic callback offer, not a claim about Delta’s actual recording. The demonstrated effect is speech in a menu that policy requires to remain silent; **these tests do not establish that a remote airline would interpret “agent” as acceptance or actually arrange a callback**. Severity is Medium as a violation of the supervised-call output boundary, and it blocks this conformance verdict.

Controls through the same classifier/dispatch path all pass (12 observations):

| Menu | Actual for both words and both modes |
|---|---|
| We can call you back. Select an option. | Silent; callback evidence detected |
| We can ring you back. For baggage, press 1. | Silent; unknown pre-option text refused |
| Please select an option. | Generic agent/representative spoken, as intended |

The second control connects the finding to this review: the callback wording that round 22 successfully rejects before an explicit option becomes acceptable when no explicit option is recognized. Additional direct output probes also reproduce generic speech on “Would you like us to ring you back?” and “For a ring back, tell us which department you need.” Those two classify as unknown without a model; they are supporting final-gate probes, not claims that the full automatic dispatch selects IVR for them. The main finding relies on the deterministic `ivr/high` example above. Supplying positive callback evidence refuses every supporting case.

**Correction:** do not treat “no parsed announcements” as evidence that an otherwise unvalidated menu is safe for generic speech. Apply a positive whole-text safety rule to that path too, retaining generic requests only for known harmless contexts. Include callback paraphrases such as “ring you back” in regressions for both explicit-option and no-option menus. A synonym addition alone fixes this example without fixing the structural assumption. Preserve the coordinator’s rule that a callback offer silences the whole menu.

## Delta-20 fixes and approved-name conformance

**N16-G is fixed for the reported cases.** A business name is now matched against the approved task names, with sentence boundaries retained. All five old callback/surcharge intro families refuse in both modes and both output paths. Removing punctuation does not restore the bypass. The whole-menu checks may skip a recognized intro because its variable name now has an explicit source of approval.

**N16-H is fixed for the reported cases.** All four compact amount forms (`35USD`, `USD35`, `35p`, `35c`) now refuse in both modes and both paths. The earlier currency symbols, numeric/spoken amounts, decimal, word key and star controls retain their expected behavior.

There are two deliberately separate reruns:

| Matrix | Result with Delta names approved |
|---|---|
| Original delta-20 menu wording | 122/132 original expectations match. All 36 previous unsafe observations now refuse. The other ten are extra refusals of unapproved Sample Air greetings/generic-agent controls. |
| Delta-20 wording with Sample Air → Delta Air Lines, Sample → Delta | **132/132** match; approved greetings now work and all unsafe variants refuse. |
| Original delta-19 wording | 8/10 match; only the two unapproved Sample Air greeting controls refuse. |
| Delta-19 wording with the same Delta substitution | **10/10** match. |
| Delta-17 extended matrix | **52/52** match, unchanged menu wording. |
| Original delta-15 matrix reused in earlier reviews | 59/66 original expectations match; the same seven decided stricter refusals. |

The earlier fixtures are copied into this scratch folder. Session construction is adapted to set `options.intro_names` to the two approved Delta names. The two `*_delta.py` fixtures make only the documented business-name substitution; raw copies retain the earlier menu strings and expectations. The delta-20 diagnostic calls also receive normalized names so their logged explanation matches the live gate configuration.

New name-boundary and authority probes are **96/96**: both approved names and uppercase/harmless fixed-intro combinations work; incomplete names, extra words, a sentence split within the longer name, unknown names, payment/callback additions and amounts refuse. A comma inside “Delta Air, Lines” normalizes to the approved tokens; a period inside “Delta Air. Lines” does not span the sentence boundary. Missing approval and approval of only the other alias refuse each named intro. Bare “Thank you for calling” remains usable.

Delta-17’s punctuation/shared-key, split phrase, callback, buy/deposit/bill, pound and one-key-per-menu cases pass. All four in-flight tool probes pass (timeout on/off × owner acceptance on/off). The original four active/queued prepared-reply cases also pass. The seven old-matrix differences are still the accepted decline refusals in both modes, callback-staying key refusals in both modes, information-only spoken `one`, and staying `no` refusals in both modes.

## Harness and task-option path

`tools/trial/hold.py:374–381` passes only `row.get('intro_names', [])`. The label remains the title. `CallService.request_call()` validates names before creating the task, stores them, and returns an approval note naming them. The Hermes tool forwards the option and includes `intro_note` in its approval message. `CallSession.__init__()` loads the stored options; both speech and keypad checks read them. The old label-based suggestion in L1 section 35 is superseded by L6 round 14 and is not the implemented authority source.

A socket-free integration fixture runs real `approval()` on a synthetic row, real L6 `task_args()`, real `request_call()`, a scratch SQLite store, and the real CallSession constructor. Captured menu outputs then use the stored options. The service is never started and no approval/dial command is issued.

| Case | Stored names | Delta greeting result |
|---|---|---|
| Explicit approval list, label not used as authority | Delta; Delta Air Lines | Key and speech sent |
| Missing field, label is Delta Air Lines | none | Key and speech refused |
| Null field | none | Key and speech refused |
| Empty list | none | Key and speech refused |

The exact list appears in the approval note, survives storage, and loads into the session along with information-only mode and callback/payment/change restrictions. Ten malformed/blocked-name inputs are rejected by the real request method. The integration fixture has **18/18** passing observations and asserts the fake provider adapter was never called. Its first setup attempt used the upstream raw fixture question fragment without running approval normalization and was correctly rejected; `task_flow_setup_attempt.log` preserves that fixture error. The corrected fixture runs the real approval normalization and passes.

The selected L6 recovery/hold suite passes **38 tests**, including the new label-authority and missing-field tests plus the existing stop/dispatch, create draining, late-SID hang-up, unresolved journal/startup, masked reconciliation and no-automatic-SID-less-clear regressions. The different-call collision probe also passes: recovery refuses, the marker remains, and the simulated unresolved call remains live rather than being falsely declared resolved. No new harness defect was found. The dry-menu change uses the first approved name or the unnamed “Thank you for calling”; the socket-based full dry rehearsal was not run under the no-network limit.

## Other checks and artifacts

| Artifact | Result |
|---|---|
| `rules.log` | 9 tests, OK |
| `r20_rules.log` | 5 tests, OK |
| `r21_rules.log` | 7 tests, OK |
| `r22_rules.log` | 2 socket-free rule tests, OK; request validation/approval note covered independently above |
| `approved_edges.log` | 124 observations: 112 match; 12 callback-without-choice direct-output failures supporting N16-I |
| `callback_no_options.log` | 16 classifier/dispatch observations: four N16-I speech failures, 12 passing controls |
| `task_flow.log` | 18/18 |
| `l6_selected.log` | 38 tests, OK |
| `l6_residual.log` | 1 test, OK |
| `pin_plants.log` | 12/12 expected outcomes, unchanged from delta 20: nine payload plants caught; timestamp/ID collision excluded; two nested metadata-key exclusions documented |

[CASE-RESULTS.md](CASE-RESULTS.md) lists every matrix, integration and PIN observation. Exact menus, outputs, events and detector evidence remain in the named JSON logs and scripts in `fixtures/`. Observation scripts return zero when execution completes; inspect `pass_case` rather than treating zero as conformance. No full-suite pass is claimed.

Reproduce from this scratch folder:

```text
/usr/bin/python3 runner.py baseline edges extra intro_edges extra_delta intro_edges_delta rules r20_rules r21_rules r22_rules l6_selected l6_residual pin_plants approved_edges task_flow callback_no_options
```

Test subprocesses use `sandbox-exec` with all networking denied, including loopback, and real `~/.hermes-line/` reads denied. The separately authorized approval-file read occurred outside the test processes and printed only label/name fields. All writes are in delta-21 scratch. Source/toolchain are read-only to tests; environment is allowlisted with scratch HOME/TMPDIR/HERMES_HOME, disabled bytecode and a nonfunctional fixture `hermes`. Limits are 60 CPU seconds and 60 seconds wall time per script, 8 MiB per file, 128 open files, process limit 1024, no core dumps; there is no aggregate memory/disk quota. No tracked Graft index was present in the export, so source/diffs were used without a graph rebuild or update.

No network, calls, provider access, real credential access, other `~/.hermes-line/` reads, package installations, repository writes, commits or pushes occurred. Live Delta wording/STT, remote provider behavior and spending readiness remain untested. The combined NO is based on N16-I, not on the intended refusal of unapproved business names or any regression in the reviewed harness.
