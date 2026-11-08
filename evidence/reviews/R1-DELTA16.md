# R1b delta 16 — L6 round 11, N18/N19

**Hold-command verdict, harness side: NO.** The original N18 response-loss probe and N19 stop-during-balance probe now pass their corrected assertions. N19's reviewed shutdown paths are fixed. N18 remains open because a nonempty recovery window can falsely resolve a missing create and remove its startup block while that create's leg remains live.

Reviewed commit: `0e477ac3964d7162d5f14653fcdfa2c80fd17970`, exported with local `git archive` to `source/`. Main was `138619742b26298aeb6ac8e3acf3a49f6c580dec` at the final comparison; `tools/trial/`, `tests/l6/`, and `docs/lanes/L6.md` had no differences from the reviewed commit. Repository status was clean. References below are to the exact exported source.

## N18-R — Medium — a different call in the narrow window can clear an unresolved create

**Source:** `tools/trial/hold_recovery.py:85–91,126–169`; `tools/trial/hold.py:337–369,538–568`; startup at `tools/trial/run.py:1012–1016`.

Recovery filters Calls by exact To/From and an interval from five seconds before the attempt to thirty seconds after it. It validates the returned rows, ends the visible live matches, and checks pagination. It then marks the unresolved create `ended` whenever that list is nonempty and all returned calls are terminal. There is no positive correlation between a returned SID and the missing create. A complete page for the selected interval does not establish that the missing create is included in that interval.

**Local reproduction, `fixtures/review_probes.py`:**

1. The real HoldAdapter's fake HTTP boundary accepts a business create and loses the response. Production code persists an unresolved intent with no SID.
2. Provider-state fixture contains the accepted call with creation time T and start time T+60, representing a queued start beyond the journal's T+30 upper bound. It also contains a different completed outbound call with the same To/From, start time T−2 and creation time T−3.
3. Recovery time is advanced to T+61 without sleeping. The fake Calls list implements the requested To/From/StartTime filters and returns only the earlier completed call, with no next page. This is a complete result for the requested interval; it is not a foreign or malformed response.
4. Real `recover()` returns `True`, reports completed recovery, and removes `hold-recovery.json`. The missing-create call remains `in-progress` in the external fake provider-state file.

Observed result in `review_probes.log`:

```json
{"case":"nonempty partial window","recovery_success":true,"marker_exists":false,"missing_create_status":"in-progress","matched_status":"completed"}
```

The boundary violation is false successful cleanup of an unresolved provider create, removing the startup gate that is supposed to prevent another trial while a call may remain. It requires a same-To/From collision plus a missing call outside the searched interval (or otherwise not yet visible), so severity remains **Medium**. No real orphan call, provider delay, charge or additional live dial was produced. The fixture demonstrates the harness's response to those provider records; actual Twilio queue/index timing is unverified. Round-11 documentation itself recognizes queued starts outside the interval, but handles only the empty-window case conservatively. A matching earlier call turns that conservative refusal into a false success.

**Smallest effective fix:** keep an ambiguous create unresolved until evidence identifies that create and confirms its termination, or an explicit coordinator reconciliation establishes that no leg remains. Continue recording and ending discovered candidates, but do not mark the intent resolved merely because an arbitrary nonempty window is terminal. If narrow Calls reads cannot establish identity/completeness, retain the marker and refuse new dispatch. Widening the interval or waiting a fixed delay alone does not prove correlation. Add the exact nonempty-window counterexample alongside the empty-window test.

## Assessment of the requested fixes

| Control | Result and evidence |
|---|---|
| Durable unresolved intent before each business/owner create | Pass for tested windows. `hold.py:278–286` calls `journal.intent` before Calls POST. `hold_recovery.py:80–91` persists both the run journal and startup marker. `inbound.py:15–29` flushes/fsyncs the temporary file, replaces the destination, and fsyncs its directory. A crash before the startup-marker save prevents reaching the POST; a crash after provider acceptance leaves the marker. |
| Response loss and process death before SID persistence, both roles | Pass. Fresh recovery finds/ends the accepted fake call when it appears in the selected window. Actual child-process `os._exit(42)` tests preserve provider-state files outside the creator's memory. Known business-leg cleanup does not erase an unresolved owner intent. |
| Startup block | Pass when recovery remains unresolved. `run.py` calls hold recovery under the global run lock before `execute`. A new probe invokes real `main()` with an empty fake recovery window and verifies `execute` is never called. Offline hold dry refuses pending recovery before credentials. N18-R is a false-resolution bypass of this otherwise functioning startup block. |
| Narrow Calls read | Partially correct. Exact To, From, StartTime bounds and PageSize 1000; no arbitrary pagination URL is followed. Every returned row's SID, destination, source, direction and time is validated before acting. Empty/foreign results refuse. Valid visible matches from a paginated result are ended, but the marker remains. A nonempty result still does not prove that the original create was found: N18-R. |
| Recovery failure and known-leg termination | Pass for tested cases. Discovered SIDs are persisted before further blocking work; terminal status is re-read after ending live legs. Cleanup preserves unresolved rows and unconfirmed SIDs. API errors or incomplete results propagate a startup Stop rather than a successful recovery. |
| Concurrent journal writers | Pass for covered merges. File locking and merge logic preserve another reconciler's SIDs, match lists and a newly added owner intent. `clear()` reloads the latest marker and requires all discovered SIDs confirmed ended. A live creator with an unfinished request prevents clearing; a dead creator can be reconciled. PID reuse can conservatively block recovery. |
| Stop/dispatch commitment | Pass for tested interleavings. `hold.py:206–238,258–286` exposes a stop Event, serializes final commitment under the adapter lock, and checks stop/expiry/deadline after balance and durable-intent work. Stopping during either step prevents Calls POST. Approval is reread after balance; expiry crossing balance refuses dispatch. |
| In-flight draining | Pass. `active` covers preparation, serialized dispatch, HTTP and SID persistence. Cleanup stops admission and waits for active work. The simulated fifteen-second drain-timeout branch reports `unresolved_cleanup`, retains the marker, and does not claim successful cleanup. The test forces the timeout result; it does not wait fifteen seconds. |
| SID returned after stop | Pass. A committed create's late SID is persisted, ended and re-read as terminal; cleanup drains it before success. Persistent end failure retains recovery state, which a later fake recovery resolves. |
| Final outcome | Pass for tested unresolved cases. Cleanup failures reach the report/stream and the live `hold` command raises Stop. This is undermined by N18-R when reconciliation incorrectly changes the unresolved intent to ended. |

A create committed before stop is treated as in flight and cleaned up; the synchronization does not promise that every network byte or physical ring precedes stop. That is the intended N19 distinction. No new N19 finding was established in this scoped review.

## Executed cases

The fresh selected suite includes the following fourteen round-11 regression methods (some contain both-role subcases):

| Case | Result |
|---|---|
| Response loss, business and owner; cleanup then fresh recovery | Pass |
| Process exit before SID persistence, business and owner | Pass on focused rerun |
| Empty or foreign Calls window | Pass; startup remains blocked |
| Stop while balance read is blocked | Pass; no create, cleanup drains |
| Expiry crossed during balance read | Pass; no create |
| Late response after stop | Pass; leg ended before cleanup success |
| Late SID with persistent hangup failure | Pass; marker retained, subsequent recovery succeeds |
| Stop during durable intent | Pass; no HTTP commitment, record becomes not_dispatched |
| Missing SID; multiple matching calls | Pass for matches visible within the fixture window |
| Paginated window | Pass; visible match ended, unresolved marker retained |
| Recovery while creator still in flight | Pass; cannot clear marker |
| Drain timeout report and stream | Pass; unresolved_cleanup |
| Late writer versus reconciler-discovered SIDs | Pass; extra SID preserved and blocks clearing |
| Stale reconciler versus new owner intent | Pass; both intents preserved |

Ten additional existing HoldTests pass: approval validation; no repeat after ambiguous create; balance polling; remaining lifetime/owner window; both-leg/exact-conference cleanup; foreign-conference rejection; offline startup refusal; pricing fallback; legacy known-SID recovery; and role/no-redial/human-before-owner ordering.

The earlier delta-14 probe was read and retained unchanged as `fixtures/probe_original.py`. Its corrected version, supplied in round 11, was copied to `fixtures/probe_fixed.py`, compared against the original setup and executed: **both corrected assertions pass**. This checks original response loss and stop during a blocked balance read through production adapter/recovery methods.

Two additional reviewer cases:

| Case | Expected | Actual | Conformance |
|---|---|---|---|
| Real main with unresolved empty recovery window | Stop before execute, retain marker | Stop before execute, marker retained | Pass |
| Nonempty narrow window contains an earlier completed call but excludes the lost-response create | Retain unresolved marker / block next run | Recovery success, marker deleted, missing leg live in fake provider state | **Fail — N18-R** |

The second probe intentionally asserts the defect to retain reproducible evidence. Its unittest `OK` means the unsafe result was reproduced, not that conformance passed.

## Commands, artifacts and limits

Run from this scratch folder:

```text
/usr/bin/python3 runner.py selected_tests probe_fixed
/usr/bin/python3 runner.py review_probes
```

`selected_tests.log`: 24 methods attempted; 23 passed initially, while the process-death method had two environmental subcase errors because the initial per-user process limit of 64 prevented spawning. This was not an application failure. After changing that limit to 1024, the focused `review_probes` run passed the process-death method for both roles and the two reviewer cases. Thus all 24 selected upstream methods passed across these runs; no full 90-test suite pass is claimed. `selected_tests-initial.log` preserves the initial output. `probe_fixed.log` and `review_probes.log` retain the other results. The real-engine dry test was excluded because it opens loopback sockets and this task prohibits network use.

All target execution used `sandbox-exec` with all networking denied, including loopback; target/toolchain read-only; writable fixtures confined to this delta-16 scratch folder; all real `~/.hermes-line/` reads denied. The permitted approval file was not needed or read. Synthetic approval records and fake credentials/providers were used. No provider connection, phone call, gateway access, installation, commit or push occurred. All new files, including the disposable export and Graft's local query location, are under this scratch folder. Graft returned no matching nodes; the repo itself was never used as a Graft working directory in this review.

The runner uses an explicit environment allowlist: scratch HOME, TMPDIR, HERMES_HOME; PYTHONDONTWRITEBYTECODE=1; source plugin PYTHONPATH; local interpreter/HERMES_AGENT_DIR; and a PATH containing a nonfunctional fixture `hermes` plus system binaries. Limits: 60 CPU seconds, 60-second subprocess wall timeout, 8 MiB per file, 128 open files, process limit 1024, no core dumps. Initial attempts to set address-space/data/RSS limits were rejected by the host, before target execution. There is no enforced aggregate memory or disk quota; this is a limitation relative to the security-audit skill's full sandbox prescription. Its focused evidence/severity guidance was applied within the coordinator's explicit bounded fake-test request and artifact scope; no full audit or independent-agent workflow was performed.

This is an offline, source-and-fake-provider delta review. It does not verify Twilio list filtering/indexing in production, actual carrier termination latency, rate/budget readiness, or engine menu safety. N18-R must be resolved before a harness-side yes; the separate engine verdict and coordinator preflight remain independent requirements.
