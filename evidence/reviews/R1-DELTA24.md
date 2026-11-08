# R1b delta 24 — speaking to automated assistants

**Delta retry verdict: NO.** The recorded run now receives fixed routing speech, and rate/quantity limits work in the probes. However, the detector can promote a named bot to HUMAN and keep an unnamed human in the assistant path. Mixed unsafe offers also bypass the documented silence rule. These findings are independent of the two known L1 tests excluded by the coordinator.

Reviewed main export `a4d326728e7c654571db2caf9735c875bda22919`, containing L1 `74a9d2f98c60ad4d55da29ee752d3189ffe5ee3c`, L2 `be38d65fdc750da2dbca779959991e2c65ef5598` and `41877a756f1ad8d23cdade3be0aebfa92ce17de6`. Engine files match the L1 commit; detector files match L2 round 6. Read assistant.py, machine hooks/finish handling, detector/greeting/tracker code, r25 tests, L1 section 38, and L2 rounds 5–6. The brief hash is unchanged. Source references below are relative to the exported tree.

## N20 — Medium — a bot's name overrides its preceding explicit automation declaration

Source: `plugin/line/detect/__init__.py:183–186`, `plugin/line/detect/greetings.py:9–20`, `plugin/line/engine/machine.py:_track_assistant`, `_on_detection`, `_on_human`.

Synthetic ground truth: the same bot says both lines, with no transfer:

1. “I'm Delta's automated assistant.” → automated_assistant; engine says “Representative.”
2. “My name is Alex. How can I help you?” → **human/high**; engine sets `human_confirmed=True`, speaks “Thank you. Please hold for just a moment while I connect Fixture owner.” and invokes the owner-ring boundary, reaching `ringing_owner` in the fake session.

The detector's reason is “personal-name greeting overrides stale assistant state.” In this case the automation evidence is recent and belongs to the same synthetic speaker. L2 accepts the introduction because `active` itself satisfies the human-rule condition. L1 also clears its assistant declaration on a personal introduction. Naming oneself is therefore sufficient to cross the human boundary despite the preceding explicit bot declaration.

Control: “My name is Alex, your virtual assistant. How can I help?” in one utterance remains automated_assistant and receives “Agent.” Explicit automation takes precedence only when repeated in the current utterance. Splitting a bot's declaration and name across turns changes the result.

Impact: human-only fixed speech is emitted to a bot and the engine requests an owner ring for a bot. The actual provider/ring boundary was faked; no phone rang. This is not a claim that a transcript can prove acoustic liveness. It demonstrates that the reviewed rule makes a high-confidence human transition on an ambiguous name even with contrary recent evidence.

Correction: retain recent explicit bot evidence across a separate name introduction. Use an uncertain/confirmation transition when a new party is plausible; do not treat a personal name alone as proof that the bot state is stale. Preserve the successful genuine named-agent cases without requiring every agent to use one exact greeting.

## N21 — Medium — unnamed agents remain in the bot path; transfer recognition differs between layers

Source: `plugin/line/detect/__init__.py:185–188` and `HoldTracker.update` assistant/hold flags; `plugin/line/engine/machine.py:_track_assistant`, `_assistant_state`, `_maybe_confirm`.

Synthetic ground truth: a human takes over after the bot, without a personal-name introduction.

| Sequence after “I'm Delta's automated assistant.” | Observed engine behavior |
|---|---|
| “Hello, how can I help you?” → “Can I have your confirmation number?” → “Hello? Are you there?” | All automated_assistant; Baggage question / Agent / Agent; no confirmation question or owner ring |
| `[music]` → “Hello, how can I help you?” → “May I have your confirmation number?” | Music is silent hold_music, but subsequent human lines revert to automated_assistant; Baggage question / Agent; no ring |
| “Please wait while I connect you.” → “Hello, how can I help?” | Transfer itself is automated_assistant and gets Agent; greeting gets Baggage question; no ring |

The category in these synthetic tasks is explicitly set to baggage. The failure is the bot classification and missing human handoff, not the choice of that permitted category phrase.

The third sequence exposes a concrete layer mismatch. L1 recognizes “Please wait while I connect you.” as a transfer and emits `assistant_handoff`. L2's `_transfer` requires forms such as `connect you with/to`, so it misses this sentence and returns continuing automated_assistant. L1 immediately sets `assistant_declared` again from that result. A simple transfer announcement therefore receives routing speech and leaves the new agent trapped behind the bot latch.

Positive controls: “Let me connect you to an agent.” is silent hold_message. A subsequent named Dana greeting reaches `ringing_owner`. An unnamed “Hello, how can I help?” after that recognized transfer is UNKNOWN, gets the real engine's live-person confirmation, and “Yes, I'm a live person.” then reaches `ringing_owner`.

A bare greeting cannot conclusively identify the new speaker; the issue is treating ambiguity as continuing bot certainty and suppressing the available confirmation path. The capped phrase loop eventually goes silent without resolving this error. Align transfer recognition between L1 and L2, and permit an appropriate confirmation transition after plausible handoff/hold boundaries even without a name. Do not merely label all unnamed greetings human, which would worsen N20.

## N22 — Low — data-word priority bypasses unsafe-offer silence

Source: `plugin/line/engine/assistant.py:104–109`.

`choose()` returns `agent` on a data-word match before consulting `unsafe_offer()`. These probes follow an explicit bot declaration, with ten seconds between turns; both information-only and other restricted modes produce the same results:

| Bot prompt | unsafe_offer() | Actual | Documented requirement |
|---|---|---|---|
| What phone number should we call you back on? | true | Agent. | silence |
| Would you like to make a payment? | true | Agent. | silence |
| Do you want to change the date? | true | Agent. | silence |
| Would you like us to call you back? | true | silence | silence |
| Can I get your confirmation number? | false | Agent. | Agent. |

Six mixed-offer observations send speech where L1 round 25 specifies nothing. The broad data words `phone`, `number`, `payment` and `date` cause the early return. Fix the unsafe-offer precedence and cover overlaps, while retaining Agent for ordinary identifier requests.

This is a silence/conformance issue, not a demonstrated data leak or agreement to payment/callback/change: the output remains the fixed “Agent.” No yes or sensitive value was emitted.

## Delta run 1 replay

Replayed all eight business transcript entries from `evidence/trial/call_b669b105d4774a49/trial.json`, in order and using their recorded relative timestamps. The recorded intervals were advanced with a controlled monotonic clock; the replay did not wait in real time. Both task modes produce the same result:

| Business line | L2 label | Engine output |
|---|---|---|
| Thanks for calling. | unknown | live-person confirmation |
| Hi. | unknown | fixed re-ask |
| I'm not a live person. | automated_assistant | Representative. |
| I'm Delta's automated assistant. | automated_assistant | silent: spacing |
| For the known traveler number, are you trying to add or update it? | automated_assistant | silent: spacing |
| Or are you trying to verify whether it's already on a reservation? | automated_assistant | Agent. |
| I can give you a moment if you need it. | automated_assistant | Agent. |
| Same patience line again | automated_assistant | Agent. |

Assistant actions occur at relative business-transcript times approximately 19.390, 28.070, 41.814 and 55.687 seconds. No live-person question occurs after the explicit bot declaration; no human is inferred; the session remains IVR. The model's turn function raises if called, and no such call occurs in the replay or adversarial assistant sequences. This fixes the observed silence on the recorded bot loop, but does not establish that a real retry would reach a representative. The past transcript cannot react to the new spoken replies.

Replay uses the actual HoldTracker, CallSession tracking/detection dispatch, phrase choice, `speak`, human handling and finish methods. Session/storage/transport setup is synthetic, owner ringing is a fake state transition, and detector model access is absent. The initial disclosure handshake and audio/STT are not replayed; the fixture starts before the first business line and uses a simple synthetic configured live-person question. This is a socket-free engine-method replay, not the full Relay ingress suite.

## Other results

- **100 L2 unittest tests pass** on the reviewed source, including round-5/6 tests. The adversarial failures above are additional coverage.
- **8-second spacing and 12-per-call cap pass:** attempts at 100, 107.999 and 108 seconds send at 100 and 108 only. Continued 8-second attempts stop after 12 total phrases. A controlled clock is used; constants are not disabled.
- **Finish restrictions pass:** before a human, information-only refuses failed, callback_expected and done; other restricted tasks refuse failed/callback_expected and retain the documented done behavior.
- **Post-human assistant path refuses output:** after the named-human transition, a direct `_assistant_reply` attempt produces no additional frame.
- **Confirmation questions:** “Is that correct?” receives Agent, never yes. The existing-trip question receives “No, a general question.” An open help question with the approved synthetic category receives “Baggage question.” The context-bearing “I can help with your existing booking. Is that correct?” also receives No/general. No unrestricted model prose, confirmation identifier or affirmative agreement was observed.
- **Transfer/named-agent controls:** recognized transfer is silent; named human handoff and recognized-transfer → unnamed greeting → explicit confirmation work as described above.

The two known M3 stale-command and R5 media-streams test updates are not findings and were not used to determine the verdict. The socket-based L1 suite, including its full r25 Relay tests, was not run under the no-network constraint. No full L1-suite pass is claimed. `tools/trial/` has no diff from the previously reviewed b137b1b harness; this review does not repeat the prior provider-recovery audit. The new detector/assistant findings independently prevent approval of the retry.

## Artifacts and limits

`fixtures/review.py` contains the replay, adversarial sequence tables, phrase prompts, spacing/cap, finish and post-human checks. `review.log` records each input, detector evidence, output, state and relevant events. `CASE-RESULTS.md` expands those rows for reading. `fixtures/l2.py` and `l2.log` contain the 100-test detector run.

Reproduce from this scratch folder:

```text
/usr/bin/python3 runner.py review l2
```

The observation script reports behavior; exit zero alone is not a conformance pass. Assertions cover quantitative limits, finish and post-human controls. Findings are based on the explicit recorded rows above.

All test subprocesses run under the reused sandbox-exec profile denying networking, including loopback, and real `~/.hermes-line/` reads. Source/toolchain are read-only to tests; writes are restricted to delta-24 fixtures. HOME/TMPDIR/HERMES_HOME point into scratch, bytecode is disabled, and hermes is a nonfunctional fixture stub. Limits: 60 CPU seconds and 60-second wall timeout per script, 8 MiB per file, 128 open files, process limit 1024, no core dumps; no aggregate memory/disk quota. No tracked Graft index exists in the export; source/diffs were used without rebuilding a graph. No approved-destinations file read was needed this round; the two approved names supplied in task context are retained in the synthetic sessions.

No network, calls, provider access, real credentials, protected-folder reads, installations, repository writes, commits or pushes occurred. All writes are under the requested delta-24 scratch directory. Actual audio speaker changes, live Delta responses, provider behavior and spending readiness remain untested.

Final revision check: main advanced during review to `8a394aaad1c23e4de611c04a0b3f22ee29b0d832`. The reviewed assistant.py, machine.py, detector __init__.py and greetings.py remain byte-identical to the exported source. The checkout remained clean. Tests above ran against the stated a4d3267 export.
