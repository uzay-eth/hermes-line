# R1b delta 19 — L1 round 20 and L6 round 13

**Combined airline hold-test verdict (engine and harness): NO.** The requested prior reproductions now pass under the revised rules, including whole-menu silence on callback offers and no automatic resolution of a SID-less create. A remaining engine case still sends payment selections because arbitrary text before the first option is discarded as a greeting.

Reviewed main export `8286f0a50d95ef46e4f454e7d5044396fe07ed7b`, containing L1 `7de70aa35986b2216a0942f39a1884279c441d19` and L6 `ec4c35767f764dd9b084f4d47deb508091079a5b`. The scoped L1 files match the L1 commit; `tools/trial/`, `tests/l6/` and L6 notes match the L6 commit. Final repository status was clean. Reviewed source/tests and L1 section 32 / L6 round 13 against deltas 17 and 18. All references below are relative to the exported source.

## N16-F — Medium — payment context before the first option is treated as a greeting

**Source:** `plugin/line/engine/menu_gate.py:34–47,115–135,157–172,184–220`.

Whole-menu blocked-word checks repair the reported punctuation examples, but `clauses()` still drops arbitrary pending fragments before the first announcement (`pending = []`, line 130), labeling them a greeting without checking that they are harmless. The positive-form check then sees only the remaining safe-looking option. The blocked list does not include debit/debits or deduct/deducts.

Fresh real-engine tests, in **both information-only and other restricted modes**:

| Menu | Requested output | Expected | Actual |
|---|---|---|---|
| Selecting baggage debits your account. For baggage, press 1. | key 1 | block | **sent** |
| Selecting baggage debits your account. For baggage, say agent. | spoken agent | block | **sent** |
| Selecting baggage deducts thirty five dollars from your balance. For baggage, press 1. | key 1 | block | **sent** |
| For baggage, press 1. Selecting baggage debits your account. | key 1 | block | blocked |
| Welcome to Sample Air. For baggage, press 1. | key 1 | send | sent |

The first three are six unsafe observations across the two modes. The trailing-context control is significant: moving the same payment sentence before the option changes a refusal into a send. The normal greeting remains usable. No real debit or payment was performed; the demonstrated effect is an unauthorized payment-selection signal at the existing engine output boundary. These synthetic menus are not claims about the live airline's wording.

**Severity: Medium**, as in the earlier menu findings: the selection is limited to the existing call, but directly violates the information-only/no-payment requirement.

**Smallest effective correction:** do not discard arbitrary pre-option context as a greeting. Only explicitly recognized harmless introductory text may be ignored; unknown leading clauses that could qualify the selection must make the menu unusable. Apply that rule equally to keypad and speech paths. Adding debit/deduct variants is a useful regression guard but does not repair the general assumption that all unrecognized leading text is harmless. Retain the current whole-menu callback refusal and positive option forms.

This is the remaining blocker for the combined verdict. No additional harness finding was established under the revised coordinator rule.

## Revalidation of delta 17

- **N16-D:** all earlier cross-sentence, punctuation, shared-key, referent, split-phrase and callback-context examples now refuse as expected. Normal shared safe-key and pound controls pass.
- **N16-E:** the original buy/deposit/bill examples now refuse. The new leading-context case above is a residual authorization problem despite those fixes.
- **N17-T:** the original in-flight finish-tool probes now refuse after timeout, both while ringing the owner and after acceptance. `finish_outcome` remains unset and no stale speech is emitted. `worker_turn` retains the running worker's originating context; timeout marks that context over, and chunks carry their own context. The no-timeout phase-change controls also pass.
- **Queued replies and key sequences:** the original four active/queued reply cases remain suppressed after human/owner transitions; one-key-per-menu checks across turns pass.
- **Original matrix:** 59/66 match the old fixture's expectations; all seven differences are refusals listed in round-20 notes: decline in both modes, callback stay in both modes, information-only spoken one, and staying no in both modes. Callback-offer silence, including no, is explicitly accepted by the coordinator. These stricter refusals are not reported as unsafe behavior.

The unchanged delta-17 extended fixture is **52/52**, and its rule runner is **9/9**. Five socket-free round-20 WholeMenuRuleTests also pass. Socket-based end-to-end Relay tests were not run because networking is prohibited.

## Revalidation of delta 18 and the new reconciliation rule

The relevant requirement is now **no automatic clearing of an attempted create without a recorded SID**, not a claim that a timestamp-matched candidate proves request identity.

| Control | Result |
|---|---|
| T−1 different-call collision, automatic recovery | Pass: raises Stop, marker retained, missing-create leg still live in fake state. No false automatic recovery success. |
| Complete terminal candidate list, SID-less intent | Pass: `candidates_terminal_pending_coordinator`; intent/startup block retained. |
| Clearing layer | Pass: `CreateJournal.clear()` independently rejects SID-less attempted rows without explicit coordinator mode, even if stale metadata says ended. Proven `not_dispatched` intents are exempt because no create was committed. |
| Recorded SID | Pass: automatic recovery confirms that exact SID is terminal; another completed candidate cannot substitute when its termination fails. |
| Explicit reconciliation | Still requires `reconcile --i-am-the-coordinator`; successful resolution records `explicit_coordinator_reconciliation`, timestamp and candidate creation times, rather than claiming inferred identity. |
| Incomplete, invalid, empty or older-only enumeration | Pass in selected tests: no clearing, including explicit coordinator mode. |
| Live/unconfirmed candidate or active creator | Pass: retained recovery marker; all visible live candidates are attempted even if one fails. |
| Output and no-marker command | Masked SID/number output tests and no-credentials/no-marker path pass. |
| N19 stop and draining | Existing stop-during-balance/intent, late SID, failed late hangup, timeout and concurrent-writer regressions pass. |

Source: `tools/trial/hold_recovery.py:109–123,128–223`; `tools/trial/hold.py:337–371,538–568`. Startup remains under the existing run lock and calls automatic recovery before allowing another trial. The explicit coordinator path remains an operator decision over observed candidates, **not proof that an omitted provider leg does not exist**. This limitation is accepted as a policy distinction here; automatic recovery no longer makes that inference.

The copied delta-18 probe deliberately switches from explicit coordinator reconciliation to automatic recovery, matching the new requirement. Its collision/visibility setup is retained. Fresh output:

```json
{"recovery":false,"marker":true,"actual_status":"in-progress","different_call_created_delta":-1}
```

## Delta-17 extended case results

Each menu row below was run in both modes. Full exact menu strings, expected/actual outputs and events are in `edges.log` and the unchanged `fixtures/edges.py`.

| Case | Result |
|---|---|
| comma charge | Both modes pass (block) |
| colon charge | Both modes pass (block) |
| semicolon charge | Both modes pass (block) |
| period charge | Both modes pass (block) |
| newline charge | Both modes pass (block) |
| speech semicolon | Both modes pass (block) |
| speech period | Both modes pass (block) |
| shared key explicit | Both modes pass (block) |
| shared key shorthand | Both modes pass (block) |
| shared key referent | Both modes pass (block) |
| shared key safe | Both modes pass (send) |
| shared speech unsafe | Both modes pass (block) |
| callback separate context | Both modes pass (block) |
| callback shared speech | Both modes pass (block) |
| hang up intact | Both modes pass (block) |
| hang up split | Both modes pass (block) |
| place in line split | Both modes pass (block) |
| buy baggage | Both modes pass (block) |
| deposit reservations | Both modes pass (block) |
| bill baggage | Both modes pass (block) |
| standalone charge key | Both modes pass (block) |
| standalone charge speech | Both modes pass (block) |
| clean pound | Both modes pass (send) |
| one key per menu across turns (True) | Pass |
| one key per menu across turns (False) | Pass |
| tool in flight timeout=False accept=False (information-only) | Pass |
| tool in flight timeout=False accept=True (information-only) | Pass |
| tool in flight timeout=True accept=False (information-only) | Pass |
| tool in flight timeout=True accept=True (information-only) | Pass |

## Execution and artifacts

From this scratch folder:

```text
/usr/bin/python3 runner.py baseline edges rules r20_rules l6_selected l6_residual extra
```

| Artifact | Fresh result |
|---|---|
| baseline.log | 66 observations; 59 old-expectation passes and seven stricter refusals described above |
| edges.log | 52/52 pass |
| rules.log | 9 tests, OK |
| r20_rules.log | 5 tests, OK |
| l6_selected.log | 36 tests, OK: recovery SafetyTests and socket-free HoldTests |
| l6_residual.log | 1 adapted automatic-recovery collision test, OK |
| extra.log | 10 observations: six unsafe sends, four passing controls |

Exit zero for the observation scripts means the probe completed; consult each `pass_case` field for conformance. The six unsafe sends are recorded as failures. No full-suite pass is claimed. Earlier scratch files were only read; fixtures and logs for this review are retained locally under delta-19.

Tests exercise the real gate, CallSession menu output/phase handling and tool dispatch with the earlier synthetic session setup, fake model/storage/ringing/conference boundaries, and captured output frames. Timeout tests use a real executor worker and timeout plus simulated RPC delivery latency. Recovery tests use fake HTTP boundaries and external synthetic provider-state files, including actual child-process exit fixtures. No live provider was involved.

All test subprocesses used `sandbox-exec` denying all networking, including loopback, and real `~/.hermes-line/` reads. The optionally permitted approval file was not needed or read. Source/toolchain were read-only; writable fixtures were confined to this delta-19 scratch folder. Environment was allowlisted with scratch HOME/TMPDIR/HERMES_HOME, bytecode disabled and a nonfunctional fixture `hermes`. Limits: 60 CPU seconds, 60-second wall timeout per script, 8 MiB per file, 128 open files, process limit 1024, no core dumps; no aggregate memory/disk quota. Graft was queried only in the scratch export, returned no match, and was not rebuilt.

No network, calls, provider access, real credentials, package installs, repository edits, commits or pushes occurred. Live STT/menu wording, actual provider enumeration/termination, pricing and spending readiness remain unverified. The engine's leading-context bypass prevents the combined airline-test approval despite the passing prior cases and revised harness rule.
