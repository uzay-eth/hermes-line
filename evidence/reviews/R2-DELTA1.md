# Review R2 delta 1: L2 rounds 10 + 11 (detector), Delta run 3 fixes

Reviewer thread: thr_4vtu5rhu56. One round, offline only. No calls, network, provider access or `~/.hermes-line/`.
No commits. The repo was only read. Every run below used `git archive` exports in this scratch directory.

## Scope and what was run

- Round 10: `git diff ddfe40a..4f2dcd0 -- plugin/line/detect tests/l2 docs/lanes/L2.md` (commit 44e1518).
- Round 11 (scope added by the coordinator): `git diff 4f2dcd0^2..67acb35 -- plugin/line/detect tests/l2`.
  `4f2dcd0^2` is 44e1518. Main is now f0871da. It has no detector change after 67acb35.
- Exports: `base/` = ddfe40a (before), `tree/` = 4f2dcd0 (round 10), `main/` = f0871da (round 10 + 11 + L1 r39).

| Run | Result |
|---|---|
| `tests/l2` at 4f2dcd0 | 137 tests OK, 0.98 s |
| `tests/l2` at f0871da | 139 tests OK, 0.99 s |
| L1 `test_r27_new_party.RealDetectorN21Tests` at 4f2dcd0 (hermes venv) | **FAIL**: `timed out waiting for state ringing_owner (now on_hold)`. This reproduces B15. |
| L1 `test_r27_new_party` + `test_r39_hold_queue` at f0871da | 26 tests OK, 185.6 s |
| My probes `probe.py` / `probe2.py` on base, r10 and main | `probe-{base,tree,main}.json`, `probe-summary.txt` |

The round-10 diff changes no existing test expectations. Every changed or added file under tests/l2 is new.

## Each defect: fixed or not

| # | Defect / check | Fixed? | Evidence |
|---|---|---|---|
| 1 | Recorded hold announcements after hold has begun are labelled possible new party | **Yes for Delta's observed script.** **Partly in general** (see backlog BL1). | `test_delta_run3.test_recorded_queue_replay`: 0 unsafe of 25 business turns from turn 10 on, down from 23 before. Turns 20–41 are all `hold_message`, with and without the overconfident fake model, and no model calls happen during the queue. The 10/10 held-out announcements give `hold_message` after four kinds of queue start. My probes: "Thank you for your patience." and "We appreciate your patience…" changed from PNP to `hold_message`. Greeting-led announcements are still PNP, 40 of 40 repeats (BL1). |
| 2 | Automated yes/no prompts are `automated_assistant`/`ivr` | **Yes** | Turns 12–14 (the messaging offer, "Please say yes…" and "Or you can say, no.") are `automated_assistant`. All 4 held-out prompts too. The new rule (`__init__.py`, the `say (yes\|no)\|press or say` check) runs before the person rules. |
| 3 | `possible_new_party`/`human` during hold needs person cues | **Yes as gated, but the cue set is wrong**: too narrow for agents (B1) and too broad for "thank you for holding/waiting/calling" recordings (BL1) | The queue gate in `classify` (`queue_active and not person …`). |
| 4 | No new false humans (bots, recordings) | **Yes, none new** | No probe line changed from a non-human label to `human`. Two pre-existing false-human paths are unchanged (BL3). |
| 5 | No missed humans (real agents after hold, named or unnamed) | **No: blocking B1** | 13 of my 22 agent openers changed from PNP to `hold_message` or `automated_assistant`, the same under both queue starts. See B1. |
| 6 | B15: "Yes, I'm a live person." after our question was labelled `hold_message` | **r10: no. r11: yes.** | At 4f2dcd0 the N21 real-detector test fails. The cause: "Hello, how can I help?" uses up `_question`, so the clear yes falls into "queue speech without person cues". After r11 the same reply is PNP (`queue_active and question is None and clear_yes`), and in the 10 s window it is `human`. My probe agrees: a reply 13 s late changed from `hold_message` (r10) to PNP (main), and in-window gives `human`. `test_hold_affirmative` covers every `CLEAR_YES` entry, with and without a greeting in between. Main's N21 + r39 tests pass. Replies that are affirmative but not on the shared list are still `hold_message` (BL2). |

R11 itself is correct and narrow. The early return happens only in queue phase, with no question in the window, and only for an exact shared clear yes. It can only produce PNP, never `human`. The only extra cost is one engine question if a recording says exactly "Yes." or "Speaking.".

## Blocking findings

### B1 (High): real agents' first lines after hold are labelled hold queue speech or automated assistant

Round 10's `_person_cue` (`plugin/line/detect/__init__.py`) is anchored at the start of the line. It accepts only
`hello|hi|hey`, "thank(s|you) for holding/calling/waiting", "how can/may I help/assist" and "may/can I have", each
optionally after "sorry|well|okay". It also accepts named greetings (`named_greeting`, `_agent_introduction`,
`_named_role`), but those miss a name that comes after the organisation. During the queue phase, any line without a cue
returns `hold_message` (medium), or `automated_assistant` if `_assistant` matches. Three earlier rules also override a
person cue: `_recorded_tip`, the `wait time` rule before the gate, and `_assistant`. Before this round, every one of the
lines below was `unknown/low + possible_new_party`, and the engine then asked the live-person question. Probes on main
(f0871da), the same after a Delta-style queue start (bot, transfer, wait time) or a plain one ("Please hold.",
`[music]`):

| Agent line (after hold) | base | main |
|---|---|---|
| Thank you for your patience, how can I help you? | PNP | `hold_message` |
| Delta reservations, how may I help you? | PNP | `hold_message` |
| Delta Air Lines, this is [NAME], how may I help you? | PNP | `hold_message` |
| Good morning, thank you for calling Delta, how can I help? | PNP | `hold_message` |
| Good evening, this is Kim with Delta. / Good afternoon. | PNP | `hold_message` |
| Delta, this is Mark. / Reservations, this is Ana. | PNP | `hold_message` |
| Sorry for the long wait, how can I help? | PNP | `hold_message` |
| Sorry about the wait time, how can I help you today? | PNP | `hold_message` (high, from the wait-time rule) |
| Who am I speaking with? / Yes, hello? | PNP | `hold_message` |
| Hi, thanks for holding, please have your confirmation number ready. | PNP | `hold_message` (high; `_recorded_tip` beats "hi") |
| Hi, I see you're looking for help with a refund. | PNP | `hold_message` (high; "looking for") |
| Can I get your confirmation number? / What's your confirmation number? | PNP | **`automated_assistant`** |

Still fine on main: openers that start with a name or a listed greeting ("Thanks for holding, my name is Sarah…",
"Thank you for calling Delta Air Lines, this is Kim speaking…", "This is Mark with Delta…", "Hello?", "Can you hear
me?", "Okay, how can I help you?", "May I have your confirmation number?").

Consequences in the engine on main (`machine.py` `_on_detection`):
- `hold_message` means a silent `on_hold` and no question. With the probe "Thank you for your patience, how can I help
  you?" → "I'm not hearing anything, I'll disconnect now.", both lines are `hold_message`. The agent hangs up and the
  owner is never rung.
- `automated_assistant` on a real agent's identifier request sets `had_bot_label`/`assistant_declared`. Under r39's
  phrase gate, that latest label sends the agent an assistant phrase ("Agent."/"Representative."). The agent is
  recovered only if they later say "Hello?" or "Are you there?" (probe: `automated_assistant` → PNP).
- STT splits are partly lost. In "Thank you for calling Delta." / "My name is [NAME], how can I help?", the second half
  is `hold_message`, because the queue phase ends only on `human`. Here the first half's PNP still starts the question.

This contradicts round 10's own contract in L2.md ("offer of help, request directed to the caller"). Each opener
above contains one or is a greeting. It is also what run 4 exists to test: a real agent after Delta's queue.

Suggested fix (L2 only, small):
- During the queue phase, match the person cue anywhere in the line, not only at the start: "how can/may I
  help/assist", "who am I speaking with", "good morning/afternoon/evening", and "(this is|my name is) <name>" after an
  organisation prefix. Also accept identifier requests directed to the caller ("can I get/what's your … number").
- Check these before `_recorded_tip`, before the `wait time` hold rule and before the `_assistant` branch inside the
  gate.
- Add the lines above as held-out agents and keep the Delta replay at 0 unsafe.

## Backlog (non-blocking)

- **BL1 (Medium for Delta run 4; High before any non-Delta trial; not a regression).** Greeting-led recorded lines are
  still PNP on every repeat: 40 of 40 over a 30-minute loop at 45 s, in base, r10 and main alike. Examples: "Thank you
  for holding, your call will be answered in the order it was received.", "Thank you for waiting. All of our agents
  are still assisting other customers.", "Thanks for calling Delta Air Lines.", "Hello, and welcome to Delta.". The
  cause: `_person_cue` counts "thank(s) for holding/waiting/calling" and "hello", and the boundary branch runs before
  the repeated-announcement rule. Each repeat spends an engine question. With r39's 4 per segment and 12 per call, such
  a loop can use up the per-call cap before the agent comes, which is run 3's failure in another script. Delta's
  recorded 14-minute loop (turns 20–41) has no such line. That is why this stays backlog for Delta run 4.
  - Fix: in the queue phase, return `hold_message` for a repeat of earlier queue text (15 or more characters) and for
    "thank you for holding/waiting" followed by queue language.
- **BL2 (Low/Medium).** In the queue phase, affirmative replies to our question that are not on the shared list
  ("Uh, yes.", "Yeah, I'm here.", "I am.", and late "Yes, how can I help you?") are `hold_message`. Before, they were
  PNP; on a plain hold, base labelled "Yeah, I'm here." `human`. On strict calls nothing changes for confirmation, but
  the engine goes silent instead of re-asking. On unrestricted calls it is a regression.
  - Fix: return `unknown` without PNP (the engine re-asks), as the `^(no|…|yes|speaking)` branch already does.
- **BL3 (pre-existing, outside this diff; affects non-strict calls).** A named recording ("Hi, this is Ed, Delta's
  chief executive, thank you for flying with us.") is `human/high` on its own in base and main. A bare recorded "Yes."
  right after our question is `human`. On a call with no restrictions and no bot label, before any question, the
  engine accepts an L2 `human` (`_after_question` is true before any question). That call would ring the owner on the
  recording. Not reachable on Delta: the bot label makes the call strict, which requires our question and a clear yes.
  This needs a joint L1/L2 look before unrestricted trials.
- **BL4.** `_recorded_tip` is fitted to Delta's wording ("need assistance on the go", "as a .+ member", "enjoy",
  "looking for", "please have your .+ ready", "you can now"). It also beats a greeting (see B1). The generic fallback
  (`hold_message` medium without person cues) does the real work, so the lexicon can stay narrow, but it should never
  beat a person cue.
- **BL5.** The L2 confirm window (`CONFIRM_WINDOW_MS` = 10 s from the question's send stamp) is shorter than real reply
  latency. In run 3 the first business reply came 13.9 s after the question stamp (turns 3→4). r11 covers an exact
  clear yes (PNP, then the engine's own window confirms). Any other late reply is `hold_message` (BL2).
  - Fix: align the window with L1's `confirm_reply_window_s`, or document the split.
- **BL6.** The queue phase ends only on `human`. After a PNP, the same agent's next line without a cue goes back to
  `hold_message`. Consider ending or suspending the queue phase after a PNP until the next hold signal.

## Verdict for Delta run 4

**No.** Rounds 10 and 11 fix what run 3 showed: announcements, the yes/no offer, budget burn on Delta's script, and
B15. But round 10 brings back the run-3 outcome by another route. A real agent's common unnamed or org-first opener
after the queue is labelled `hold_message` (silence) or `automated_assistant` (an assistant phrase to the agent). The
owner is then rung only if the agent repeats a greeting before hanging up. Fix B1, keep the Delta replay at 0 unsafe,
add the B1 lines as held-out agents, and re-check. If B1 is fixed and nothing else changes, my verdict becomes yes,
with BL1 tracked before any non-Delta trial.
