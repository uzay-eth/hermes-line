# R1b delta 20 — L1 round 21 leading-text rule

**Combined airline hold-test verdict (engine and harness): NO.** The exact delta-19 payment reproductions now refuse, and delta-17 phase/menu cases still pass. The intro parser nevertheless accepts a short callback or surcharge sentence as a business name and sends navigation. The unchanged harness passes the selected offline regressions under the coordinator’s no-automatic-SID-less-clear rule.

Reviewed main export `b06599a03c17bfeb3eb3b07bd9ea54ceb185d32a`, containing L1 `037077e57b0c303ac7eeac5074c04450edcf46d3` and `e90fbe4c8dd7ecd82adf26aeb7b5b13f7851d7f4`, against `evidence/reviews/R1-DELTA19.md` and L1 sections 33–34. Scoped gate/machine/r21/fixture files match 037077e; the PIN helper matches e90fbe4. `tools/trial/`, `tests/l6/` and L6 notes have no diff from `ec4c357`. References below are source-relative. No repository changes were made.

## N16-G — Medium — intro name absorbs a callback or payment sentence

**Source:** `plugin/line/engine/menu_gate.py:106–122,192–225`; `plugin/line/engine/machine.py:1196–1241`.

The parser flattens all pending introductory fragments at line 198 before checking the intro allowlist. `_intro_names()` accepts any one to four alphabetic/apostrophe words as a name. Consequently a complete new sentence can become that name, or extend an ordinary name. Names remain in `checked_tokens()`, but are checked only against the negative word/amount lists. They are not verified as names or harmless text. The fixed “thank you for calling” is exempted even when the following words are a callback statement.

Every row below was exercised through real `CallSession` output handling, in **both information-only and other restricted modes**, with both `For baggage, press 1.` and `For baggage, say agent.` appended. Expected: silence. Actual: key `1` and spoken `agent` sent in every combination (20 unsafe observations).

| Leading text | Name accepted by parser | Actual |
|---|---|---|
| Thank you for calling. We'll ring you back. | we'll ring you back | Both outputs sent, both modes |
| Thank you for calling; we'll ring you back! | we'll ring you back | Both outputs sent, both modes |
| Welcome to Sample. We'll ring back. | sample we'll ring back | Both outputs sent, both modes |
| Thank you for calling. Baggage incurs surcharges. | baggage incurs surcharges | Both outputs sent, both modes |
| Welcome to Sample Air. Surcharges apply. | sample air surcharges apply | Both outputs sent, both modes |

For example, the first keypad case yields `{"type":"sendDigits","digits":"1"}`; its spoken counterpart yields `{"type":"text","token":"agent","last":true}`. No `restricted_action_blocked` event is emitted. `is_callback_offer(..., False)` is false. As in the earlier final-gate fixtures, upstream callback evidence is absent; this demonstrates the final gate cannot enforce the rule itself in that state. An upstream positive callback detection can still veto output.

The counterfactual controls are useful: the same `We'll ring you back.` and `Baggage incurs surcharges.` leading sentences **without** an allowlisted prefix are refused in all four combinations. “We'll call you back” also refuses because `call` remains blocked. Ordinary Sample Air greetings still work. This is the residual of N16-F: unknown pre-option semantics are still accepted after wrapping them in an intro, rather than being limited to known harmless introductory text.

The callback examples violate the coordinator’s whole-menu silence rule even if the selected department is otherwise safe. The surcharge examples permit selecting a department despite explicit payment context. These are synthetic output-boundary reproductions; no actual callback, debit or surcharge was performed.

**Correction:** preserve introduction boundaries and do not infer a harmless business name solely from a short unrestricted word sequence. Bind a variable name to known safe business wording, or fail closed when it cannot be established. Preserve whole-menu checks outside precisely bounded harmless fixed phrases. Adding `ring` or `surcharge` alone would cover these examples without repairing the general arbitrary-name assumption. Keep keypad and speech treatment identical, and include punctuation-loss variants in regressions.

## N16-H — Low — compact currency amounts evade the whole-menu amount rule

**Source:** `plugin/line/engine/menu_gate.py:74–94,247–269`.

Tokenization preserves contiguous letters and digits. `_amount()` recognizes digit-only tokens or enumerated number words, so `35USD`, `USD35`, `35p` and `35c` are neither numbers nor blocked currency words. The following families all send in both task modes:

| Menu | Requested output | Expected / actual |
|---|---|---|
| For baggage, press 1. For 35USD, press 2. | key 1 | silence / sent |
| For baggage, say agent. For USD35, press 2. | agent | silence / sent |
| Same two menu forms with 35p or 35c (also each form with 35USD and USD35) | key 1 or agent | silence / sent |

There are 16 such observations (four amount forms × two paths × two modes). Controls `$35`, `£35`, `€35`, `35`, `thirty five`, `half`, `quarter`, `1.50`, and `ten dollars` all refuse in both paths/modes. Word keys and star keys still work.

This is a whole-menu payment/amount conformance gap. **The selected output in these probes is the safe baggage option; these cases alone do not demonstrate selection of a paid option.** That narrower impact is why the severity is Low. Whether a live speech transcription emits compact currency notation was not tested. Normalize or explicitly recognize conventional compact currency forms before deciding that the menu has no amount; add controls preserving announced key handling.

## PIN-test change: planted payload PINs still detected

`tests/l1/test_r13_owner_line.py:43–57` now selects transcript speaker/text and event kind/data, keeps all inspected driver inputs and summary, and recursively removes timestamp/id keys in event data.

The real `OwnerLineBase.everything()` helper and its `assertNotIn(PIN, blob)` assertion were run against fake stores/drivers. A timestamp and generated-ID collision control passes without a false PIN failure. All **nine** payload plants trigger the assertion: transcript text, event kind, nested event dictionary, nested event list, driver turns, notes, asks, briefing, and summary. Thus e90fbe4 has not turned off ordinary payload leak detection.

**Coverage observation (informational):** it removes `ts`, `call_id`, `task_id`, and `*_call_id` keys recursively, not just a proven metadata envelope. Plants under a nested `ts` or `task_id` are therefore invisible, confirmed by two controls. This is intentional key-based exclusion, not evidence that production puts PINs there. The helper should not be described as detecting a planted PIN in absolutely every nested field. Restricting exclusions to known generated metadata would make that claim stronger. No production PIN leak was established, and this test-only change is not the reason for the NO verdict.

## Prior-case reruns

| Fixture / suite | Fresh result |
|---|---|
| Delta-19 `extra.py` | 10/10 pass: original debit key, debit speech, trailing debit, ordinary greeting, spoken deduction; both modes |
| Delta-17 `edges.py` | 52/52 pass |
| Original delta-15 matrix, reused by deltas 17/19 | 59/66 old expectations match; seven intended stricter refusals |
| Small gate rules plus matrix expectation consistency | 9 tests, OK |
| Whole-menu rules | 5 tests, OK |
| Leading-text rules | 7 tests, OK |
| New intro/amount/key controls | 132 observations: 96 pass, 36 failures described above |
| PIN helper controls | 12/12 expected outcomes; nine detected payload plants, one collision control, two documented exclusions |
| Socket-free L6 SafetyTests and HoldTests | 36 tests, OK |
| Delta-18/19 automatic-recovery collision probe | 1 test, OK |

Delta-17’s punctuation, repeated/shared key, cross-sentence referent, split `hang up` / `place in line`, callback context, buy/deposit/bill, standalone charge, safe shared-key and pound cases all pass. Its one-key-per-menu checks pass in both modes. All four tool-in-flight cases pass (timeout on/off × owner acceptance on/off): `finish_outcome` stays unset and stale speech is not emitted. The original four active/queued prepared-reply cases also pass across human arrival and owner acceptance.

The original matrix’s seven mismatches are exactly the accepted stricter refusals: decline in both modes, callback staying key in both modes, information-only spoken `one`, and staying `no` in both modes. None is an unsafe send or a new finding. Whole-menu callback silence, including the staying “no,” is treated as intended.

The harness source is unchanged from the previously reviewed round-13 rule. Fresh fake-boundary checks retain startup blocking and the unresolved journal for an attempted create without a recorded SID; require explicit coordinator reconciliation; verify recorded SIDs; preserve markers for empty/incomplete/live/active-create recovery; mask output; and exercise stop/dispatch locking, draining, late-SID hang-up and failed hang-up persistence. The different-call collision probe again returns:

```json
{"recovery":false,"marker":true,"actual_status":"in-progress","different_call_created_delta":-1}
```

It does not automatically clear the unresolved attempt using another terminal call. No new harness finding was established. Explicit coordinator reconciliation remains an operator decision over visible candidates, not proof that provider enumeration omitted no leg.

## Reproduction and scope

Run from this scratch folder:

```text
/usr/bin/python3 runner.py baseline edges rules r20_rules extra r21_rules l6_selected l6_residual intro_edges pin_plants
```

[CASE-RESULTS.md](CASE-RESULTS.md) lists every observation from the matrices and PIN probes. Exact menus, expected/actual results and captured events are in `baseline.log`, `edges.log`, `extra.log`, `intro_edges.log` and `pin_plants.log`; unit-test names/results are in the other named logs. Scripts are in `fixtures/`. Earlier fixtures were copied without editing their expectations. Observation scripts exit zero when execution completes, so consult `pass_case` rather than interpreting exit zero as conformance. The new tests use real gate/CallSession code with fake model, storage, conference, ringing and transport boundaries. Recovery tests use fake HTTP and synthetic provider-state files.

All test subprocesses ran under `sandbox-exec` denying networking (including loopback) and real `~/.hermes-line/` reads. Source/toolchain were read-only; writes were confined to delta-20 fixtures. The runner uses an allowlisted environment with scratch HOME/TMPDIR/HERMES_HOME, disabled bytecode and a nonfunctional fixture `hermes`. Limits: 60 CPU seconds and a 60-second wall timeout per script, 8 MiB per file, 128 open files, process limit 1024, no core dumps; no aggregate memory/disk quota. Graft was queried only in the scratch export and returned no match; it was not rebuilt or updated.

No full socket-based Relay suite, live STT, provider, account, pricing or spending-readiness check was performed. No network, phone calls, provider access, real credentials, `~/.hermes-line/` reads, installs, repository writes, commits or pushes occurred. The original leading-context cases are fixed, but N16-G still prevents the combined airline hold-test approval.
