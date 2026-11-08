# R1b delta 18 — L6 round 12 recovery

**Hold-command verdict, harness side: NO.** The exact delta-16 collision is fixed, and the new command passes its authorization, normal-output masking and visible-candidate termination tests. A near-attempt variant still clears the unresolved attempt on the strength of a different call. This requires the missing create to be absent from the returned list; the fake-provider reproduction establishes the harness behavior, not live Twilio indexing behavior.

Reviewed `fa68da47dace96573c6216165a29e27f0fdb2e32`, exported locally into `source/`. At the final comparison main was `36ec01ba6cb2d8edaef72464bfd48908fb4d1688`; `tools/trial/`, `tests/l6/` and `docs/lanes/L6.md` had no differences from the target. Git status was clean. Read the round-12 source, tests and notes against the delta-16 report and retained probe. No repo changes.

## N18-R2 — Medium — the two-second tolerance still admits a different earlier call as identity evidence

**Source:** `tools/trial/hold_recovery.py:152–169,179–210`; clearing through `tools/trial/hold.py:538–568` and `CreateJournal.clear()`.

Replacing StartTime with DateCreated and removing the upper bound fixes the previous deterministic exclusion of a queued call. However, the new rule treats any same-To/From outbound call created at or after T−2 as identity evidence for the unresolved attempt at T. That includes calls actually created before the attempt. No pagination marker is treated as completeness, and successful termination of all visible candidates resolves the unknown attempt.

**Reproduction:** `fixtures/edge.py` reuses the round-12 `SafetyTests.collision()` setup and its older-only visibility fault. The only relevant change to the earlier call is DateCreated **T−1 instead of T−3**:

1. Production HoldAdapter records an unresolved business create after the fake provider accepts it and loses the response.
2. A different completed call has the same To/From and DateCreated T−1. The actual missing-create leg remains `in-progress` in the separate fake-provider state file.
3. The Calls list exposes only the different completed call, with no next page. This models an unavailable/not-yet-visible create, exactly as the upstream older-only test does; it is not evidence that a live provider currently behaves this way.
4. The test invokes real `run.main()` as `reconcile --i-am-the-coordinator`, using fake credentials and HTTP boundaries. The command succeeds, removes the recovery marker, and leaves the missing-create leg live in fixture state.

`edge.log`:

```json
{"recovery":true,"marker":false,"actual_status":"in-progress","different_call_created_delta":-1}
```

All visible candidates really were confirmed terminal. The error is equating that fact with identifying the unresolved attempt. A creator whose request ended with a timeout is no longer marked in-flight, so the live-PID check does not prevent this result.

**Severity: Medium**, consistent with delta 16: a narrowly scoped response-loss/visibility/collision combination can remove the startup block while an approved-destination leg may remain. No actual call, charge or subsequent dial was made. If the provider guarantees immediate exhaustive visibility, this specific missing-row scenario would be excluded; that guarantee was neither established by the code nor verified in this network-prohibited review. The current upstream older-only regression already assumes missing-row visibility must fail closed, so the tolerance boundary needs the same treatment.

**Required fix:** candidate discovery must be separated from proof that the missing attempt was accounted for. A different terminal call within timestamp tolerance cannot be sufficient proof. Retain the unresolved marker unless request-specific evidence identifies the attempt and its terminal state, or an explicit independently supported reconciliation establishes no leg remains. Keep ending visible candidates and checking their status, but do not silently promote a timestamp heuristic to identity. Merely reducing the tolerance or waiting a fixed interval is not a general correlation guarantee. Add this near-attempt case alongside the T−3 older-only test.

## What the change fixes and what the command enforces

| Check | Result |
|---|---|
| Exact delta-16 collision: earlier DateCreated T−3, lost-response call StartTime T+60 | **Pass.** Actual missing call is now included, ended and confirmed before marker removal. Earlier call is ignored. |
| Calls query scope | **Pass in source/fakes.** Only exact To/From and PageSize 1000; no StartTime or status filter, and no upper creation-time bound. DateCreated is checked locally, never substituted with StartTime. |
| Empty or T−3 older-only result | **Pass.** No identity candidate; startup remains blocked even with the coordinator flag. |
| Queued call without StartTime, created beyond the old window/deadline | **Pass.** Included as a candidate and ended. |
| Two visible candidates, including ringing | **Pass.** Both ended and each SID re-read as terminal. |
| First candidate termination fails | **Pass.** Remaining candidates still processed; marker retained while one remains live. |
| Missing/nonfinite DateCreated | **Pass.** Does not substitute a plausible StartTime; marker retained. |
| Paginated, foreign or invalid results | **Pass in selected regressions.** Refuse clearing; no arbitrary next-page URL followed. Visible valid candidates can still be ended. |
| Existing SID-less `ended` row | Source revalidates it by setting unresolved and persisting before identity lookup. It does not trust the superseded StartTime resolution. |
| Coordinator flag | **Pass.** `run.py:977–978` refuses `reconcile` without the flag before credential/provider work. This is the harness's explicit operator acknowledgment, not authentication of a human identity. |
| Global run lock / no new trial | **Pass in source.** Reconciliation runs under the existing `run.lock`, then returns before trial execution/gateway/tunnel setup or voice restoration. |
| Masked command output | **Pass for valid provider status records.** Candidate SID suffixes and To/From suffixes only, plus role, creation time, status and terminal confirmation. Full test SIDs/numbers absent from command output. The saved `hold-reconcile.json` uses the same masked candidate representation. Private operational journals intentionally retain identifiers for recovery. |
| No pending marker | **Pass.** Reports no pending recovery without loading credentials. |
| Final clearing | Confirmed terminal status for all discovered SIDs, no unfinished creator, and successful cleanup remain required. **Not sufficient to prove the unknown attempt is resolved: N18-R2.** |
| N19 shutdown/draining regressions | **Pass.** Stop during balance/durable intent, late returned SID, failed late hangup, drain-timeout reporting and concurrent journal merges remain covered. |

## Tests and artifacts

Executed with no networking:

```text
/usr/bin/python3 runner.py selected_tests review_probes edge
```

- `selected_tests.log`: **32 tests, OK** — all 22 recovery SafetyTests plus ten socket-free HoldTests. Includes the new command flag, no-marker, masking, terminal-confirmation and failure-path checks. The socket-based real-engine dry test was excluded.
- `review_probes.log`: **3 tests, OK** — the adapted original delta-16 collision; real-main empty-window block; process death before SID persistence for both roles.
- `edge.log`: **1 reproduction, OK** — intentionally asserts the unsafe near-attempt result. Exit zero is successful reproduction, not a conformance pass.

`fixtures/review_probes.py` is the round-12 copy adapted from the original delta-16 script. Compared with the original: its fake list applies StartTime filters only when the production query requests them, and the collision assertion now requires the actual missing call to be completed. Original task setup and collision timestamps are retained. Earlier scratch files were not changed. `fixtures/edge.py` adds the boundary variant using the current fixture's real coordinator-command path. `runner.py` and `fixtures/selected_tests.py` reuse the prior review runner/selection approach.

This is 32 selected upstream methods plus three probe methods (some duplicate coverage) and one new reproduction, not a full 98-test suite run. The source and raw logs are retained under this scratch folder.

## Limits

All test subprocesses used `sandbox-exec` denying all networking, including loopback, and denying real `~/.hermes-line/` reads. The optionally permitted approval file was not needed or read. The target/toolchain were read-only, and test writes confined to this scratch folder's fixtures. Environment was explicitly allowlisted with scratch HOME/TMPDIR/HERMES_HOME, bytecode disabled and a nonfunctional fixture `hermes` executable. Limits: 60 CPU seconds, 60-second wall timeout per script, 8 MiB per file, 128 open files, process limit 1024, no core dumps; no aggregate memory/disk quota.

All new writes, including source export and reports, were under `/Users/mac/.hermes/cache/scratch/hermes-line-r1d18/`. Graft was queried only in that export and returned no matching nodes. No network, calls, real credentials, provider access, installs, commits or pushes were used. Live provider enumeration/indexing, carrier behavior, spending readiness and the separate engine verdict remain outside this review.

The original N18-R example is repaired, but the broader requested invariant—never clear an unresolved attempt merely because a different call is terminal—is not yet established. N18-R2 prevents a harness-side yes.
