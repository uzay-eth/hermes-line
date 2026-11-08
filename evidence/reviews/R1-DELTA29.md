# R1b delta 29 — one quiet gate

**Delta run 3 verdict: NO.** The central gate fixes the ordinary connecting/closing timing and actual-send phrase spacing, and the live trial configuration really enables it. However, partial-only speech still has no recovery path, and a late HUMAN verdict can cause two pending live-person questions to speak together. These are reproduced engine behaviors, not missing config or provider failures.

## Revision and method

Reviewed main `38bdf6183ff96e0e9704d01f14bfea3411e32db5`, containing `85a08f72b712e7e2e9ea65f17d086772a4694f39`, against delta 28. Read the production diff, shared gate/callers, fallback logic, round-31 tests and section 43, and the actual trial config/runtime path. BRIEF's previously verified hash remains unchanged. No tracked Graft index was available; source/diffs were used without rebuilding. Main and reviewed files remained unchanged at the final check; checkout clean.

Copied the prior offline fixtures into this scratch folder. `fixtures/r31.py` adapts the delta-28 timing probes to the new asynchronous gate and the documented `unverified` outcome. Frames enter real `CallSession.offer`, consumer, speech handling, detector and engine. Real `ring_owner` runs against counted in-memory authorization/placement fakes. A deliberately delayed detector result is labelled below. A controlled monotonic clock drives the real asyncio timers; `wall_quiet.py` independently checks ordinary question/connecting timing using real elapsed time. Storage, output transport and provider boundaries are faked. No real model or provider is invoked.

## N26-R — Medium — a transcript that consists only of partials still strands the call

Source: `plugin/line/engine/machine.py:459` (`offer`) and its prompt handler at approximately `:498`.

Replay: partial prompt frames at 100, 100.3 and 100.6 seconds, then no activity through 160. Result: **zero output, zero detection events, zero questions, no last_confirm and state IVR**. This is the same partial-only gap from delta 28.

`offer` records activity, but the consumer returns immediately for a nonfinal prompt. No decision, pending line, finalization timer or recovery timer is created. The new ten-second hard cap only helps a line that already entered `speak`; there is nothing for it to release in this case. The author's continuous-partials test first sends a final named introduction, so it covers a different case.

This is not a claim of an indefinitely live call: the existing duration watchdog and trial time limits can eventually end it. It is silence for the remaining call path absent another usable final event. It can also occur during an interaction when an expected final transcript never arrives.

Needed correction: bounded recovery from abandoned partial input, with a conservative fixed question/decision and the same speech and confirmation safeguards. A hard limit on already-pending speech alone does not close this part of N26.

## N29 — Medium — a late verdict duplicates the question already waiting for quiet

Sources: `machine.py:882` (`_maybe_confirm`), `:920`–`:942` (`_ask_live_person`, `_ask_now`), and the late HUMAN route through `_bot_call_human` / `_new_party_check`.

Reproduction through the real frame queue and actual timeout/late-verdict path:

1. At 100, a final unfamiliar business line starts detection. The fake detector deliberately waits.
2. Partial frames arrive at 100.3, 100.6, 100.9, 101.1 and 101.4, keeping the quiet gate closed.
3. The fixture's one-second reply budget expires. The real timeout path schedules the fixed live-person question.
4. At 101.4 the delayed detector returns HUMAN/high. No question has yet been sent, so last_confirm is unset. The late-HUMAN path schedules another question for the same business epoch.
5. At 102.7, after 1.3 seconds of quiet, **two identical “Am I speaking with a live person?” frames are sent at the same timestamp**.

There is no owner ring or human confirmation in this probe. But two questions are spoken together, and `new_party_questions` is only **1**, because only the second task has the budget callback. The original question is not superseded: both tasks share the same business epoch, and both guards merely require a pre-human state. This can reproduce with other reply budgets whenever the late result arrives while the timeout question is still waiting for quiet.

Needed correction: one pending confirmation question per call/decision, shared by timeout, late detection and ordinary new-party routes. Deduplicate or cancel a redundant task and account for all actual sends. Setting last_confirm only on actual send is correct, but it is not a pending-question guard.

## Live config path — verified

The production hold path does **not** use the test harness's zero default:

- `tools/trial/hold.py:506` calls `api.config(...)`, then passes that cfg to `api.Runtime`.
- `tools/trial/run.py:163` constructs `EngineConfig.load(...)` without overriding quiet_before_speak_s. EngineConfig's dataclass default is **1.2**.
- Runtime passes that same cfg into `CallService` (`run.py:639`).
- The dry hold rehearsal separately uses `tests/l1/harness.py:201`, which explicitly supplies **0.0**. Its passing tests do not prove production timing.

For executable evidence without importing the account-aware trial CLI, the probe extracts the actual pure `config` function AST from `tools/trial/run.py`, evaluates it with synthetic constants and the real EngineConfig, and applies the returned config to a real engine fixture. It returns quiet=1.2; the question waits 1.25 seconds in the controlled clock. No config body was reimplemented.

A separate bare-object probe removes the attribute and confirms `getattr(..., 0.0)` sends immediately. That compatibility fallback does not disable the live EngineConfig path. No credentials, approved-destination file, gateway or provider were read or called.

## Delta-28 closure and explicit policy changes

| Check | Result |
|---|---|
| N25 connecting line | Fixed in the ordinary quiet case. Real wall clock: question gap 1.252 s; connecting line gap 1.202 s. |
| N25 fallback closing | All six fallback probes wait at least 1.2 s after trailing partial activity. Closing remains neutral. |
| N26 pending question under continuous partials | Now sends within the hard cap, and spends one slot on actual send. |
| N26 old four blocked-attempt schedule | Actual question sends equal the question counter; no budget consumed solely by unsaid attempts. |
| N26 superseded question | Dropped attempt spends nothing; replacement sends once and spends once. |
| N26 partial-only input | **Still fails**, as above. |
| N27 delayed phrase spacing | Fixed: the too-early second phrase is skipped based on the first actual send. |
| N28 outcome heuristic | Removed. All five nonempty replies in the old table are `unverified`; silence is `unanswered`. All summaries quote the reply and say answer not verified. |

Two behavioral changes must remain explicit in the handoff:

- **Quiet is now bounded, not an unconditional 1.2-second guarantee.** After eight seconds, a 0.4-second gap suffices; after ten seconds, speech is forced even without that gap. The continuous-partials probe speaks at 110.2 with only **0.3 seconds** since the last partial. That conforms to round 31's documented cap, but permits talking over continuing business speech. The NO verdict above does not depend on treating this deliberate tradeoff as a new defect.
- **Nonempty does not mean answered.** The revised result `unverified` avoids the former “answered” claim for “Please wait 2 minutes” and “I cannot quote the fee in dollars.” The persisted status mapping is still DONE for any nonempty reply; this is completion of the fallback attempt, not evidence that the fee question was resolved. Storage finalization is source-reviewed; the fixture fakes hang-up.

## Replays and retained safeguards

Delta runs 1 and 2 were replayed with all eight and nine business transcript entries respectively, preserving recorded relative timestamps and using quiet=1.2. Both finish the replay in IVR with **no human confirmation, owner ring or bridge**. Every replay output follows at least 1.2 seconds of recorded quiet. These are transcript simulations, not live audio/provider tests.

Run 1 produces the fixed question/re-ask, Representative and three Agent phrases. Run 2 produces Representative, the fixed question, Baggage question and Agent. No arbitrary model prose, sensitive data or fallback-to-bot was observed.

The inherited confirmation matrix remains **103/103 passing**, and owner-ring/control checks remain **35/35 passing**. These regression scripts explicitly set quiet=0 to isolate confirmation policy; they are not timing tests. The accepted bot-says-yes residual still requires owner keypad 1 for bridging. A real agent's “Yes, how can I help?” still fails the shared whole-reply list and may delay or prevent a genuine handoff; that remains the previously stated accepted fail-closed cost. **130 L2 tests passed.**

## Artifacts and limits

- `r31.log`: 17 session observations; 15 conform, two findings above. Exit **1** deliberately reports the failures.
- `wall_quiet.log`: independent wall-clock timing check, passes.
- `review.log`, `new_party.log`, `clear_yes.log`, `ring_gate.log`: inherited replays and regression checks, pass under their stated settings.
- `l2.log`: 130 tests passed.
- [CASE-RESULTS.md](CASE-RESULTS.md): complete inputs, labels, timing, counters, outputs and outcomes.

Reproduce from this scratch directory:

```text
/usr/bin/python3 runner.py r31 wall_quiet review new_party clear_yes ring_gate l2
```

The runner reports each subprocess status; its own status is not an aggregate verdict. Copied `quiet.py` supplies clock/fixture helpers; the active adapted timing entry point is `r31.py`, not the old delta-28 assertion set.

The relevant L1 tests were read, but no socket-based L1/L6 suite was run. No live hold command, owner call or provider-recovery audit was performed. Sandbox restrictions deny all networking, including loopback, and real `~/.hermes-line/` reads; test writes are confined to scratch fixtures and /dev/null. HOME/TMPDIR/HERMES_HOME are scratch paths, bytecode is disabled and hermes is a nonfunctional stub. Per-script limits remain 60 CPU seconds/60 seconds wall time, 8 MiB per file, 128 open files, process limit 1024, no core dumps; no aggregate memory/disk quota.

All created artifacts are under delta-29 scratch. No network, calls, provider access, protected-folder reads, installs, repository writes, commits or pushes occurred. A new owner go is not sought or implied by this NO review.
