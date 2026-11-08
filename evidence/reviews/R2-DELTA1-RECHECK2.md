# Review R2 delta 1, final re-check: L2 round 13 (93c029b)

Reviewer thread: thr_4vtu5rhu56. One short round, offline only. No calls, network, provider access or `~/.hermes-line/`.
No commits. The repo was only read. Every run below used `git archive` exports in this scratch directory.

## Scope and runs

- Reviewed: `git diff fe02425..93c029b -- plugin/line/detect tests/l2 docs/lanes/L2.md`.
- Main is 3e88214. It has no plugin, tests/l2 or fixtures change after 93c029b. Export: `main13/`.
- The code change only reorders `classify`:
  - The queue-phase person-cue branch, the near-affirmative branch and `_brand_question` now come after the explicit
    rules: voicemail, IVR `press|dial|keypad`, yes/no offer, assistant declaration, callback, transfer,
    `recorded|recording`, and `please hold|remain/stay on the line|your call is important|in the queue`.
  - The wait-time rule (`not intro`) moved below the person-cue branch.
  - The `<word> speaking` cue now excludes "be" (`(?!be\b)`).
  - Nothing else changed in the branch.

| Run | Result |
|---|---|
| `tests/l2` at 3e88214 | 145 tests OK, 1.10 s. Includes the new `test_r2_explicit_rules.py`. |
| Delta 3 replay (`test_delta_run3`, 7 tests, with and without the overconfident fake model) | 7 OK. 0/25 HUMAN/new-party markers, no model calls during the queue. |
| L1 `test_r27_new_party` + `test_r39_hold_queue` at 3e88214 (hermes venv) | 26 tests OK, 185.6 s |
| 13 negatives from N1 (`probe3.py`, `probe4.py` → `probe3-r13.txt`, `probe4-r13.txt`) | **13/13 back to their r11 labels**, under both queue starts, with and without our question |
| 22 openers × 2 queue starts (`probe.py` → `probe-main13.json`, `probe-summary-r13.txt`) | **44/44 PNP or `human`**. Identical to r12. |
| Disconnect follow-up and split / org-prefixed lines (`probe2.py`) | All PNP or `human`. "Thank you for your patience, how can I help you?" → "I'm not hearing anything, I'll disconnect now." gives PNP → PNP. |

## N1: fixed (yes)

| Negative line (in hold) | r12 | r13 |
|---|---|---|
| Hello, this is Mark. I'm not available, please leave a message. | human/high | `voicemail` |
| Hi, this is Delta. Please leave your name and number after the beep. | human/high | `voicemail` |
| Hello, this is Kim with Delta, please leave your name and number and I will call you back. | human/high | `voicemail` |
| Hi, you've reached Dana. Please leave a message after the tone. | PNP | `voicemail` |
| Thank you for calling. For reservations, press 1. / Hello! To hear these options again, press star. | PNP | `ivr` |
| Hi, to receive a callback instead of waiting, press 1. / Hi, we can call you back… Press 1 to accept. | PNP | `ivr` |
| Thanks for waiting. Say yes and we'll text you a link. | PNP | `automated_assistant` |
| This is a recorded message. Please stay on the line. | PNP | `hold_message` |
| Thank you for holding. Please hold for the next available agent. | PNP | `hold_message` |
| Hello, your call is important to us. Please remain on the line. | PNP | `hold_message` |
| Thank you for holding. This call may be recorded for quality assurance. | PNP | `hold_message` |
| You will be speaking with an agent shortly. | PNP | `hold_message` |
| Thanks for calling, I'll transfer you to the next available agent. | PNP | `hold_message` |

"Hi, this is Delta reservations. Please press 1 for English." is `ivr` again. No new `human` appears on any line I
probed in rounds 1–3.

The first review's B1 stays fixed. Every opener that was `hold_message`/`automated_assistant` on r10/r11 is still
a candidate. The B1 regressions came from heuristic rules: wait time, `_recorded_tip` and `_assistant`. Those rules
still come after the person cue.

## Blocking findings

None.

## Backlog (non-blocking)

- **BL1** (High before any non-Delta trial; not reachable on Delta's recorded script). Greeting-led recordings without
  an explicit hold phrase are still PNP on every repeat (40/40 over 30 minutes). Examples: "Thank you for holding, your
  call will be answered in the order it was received.", "Thanks for calling Delta Air Lines.", "Hello, and welcome to
  Delta.". This also covers one line that r12 widened and r13 keeps: "Good morning and thank you for choosing Delta.
  Your call will be answered shortly." is PNP; on r11 it was `hold_message`. Each repeat can spend an engine question
  under r39's caps.
- **BL2/BL5:** near-affirmatives ("Uh, yes.", "Yeah, I'm here.", "I am.") are PNP inside L2's 10 s window. They are
  still `hold_message` when later than that, and run 3's first reply came 13.9 s after the question stamp.
- **BL3** (pre-existing): a named recording ("Hi, this is Ed Bastian, thank you for flying Delta.") is `human/high`.
  On a plain hold, a bare "Yes." or "Hello, who is this?" right after our question is `human`. This matters only on
  unrestricted calls, because a strict call such as Delta needs our question and a clear yes.
- **BL7 (new note, pre-existing since before r10):** named agents whose first line contains an explicit-rule word get
  a non-person label. Probe `probe5.py` gives identical results on base, r11 and r13:
  - "Hi, this is Dana, in case we get disconnected, can I get a callback number?" → `automated_assistant`. On a
    restricted call that sends an assistant phrase to the agent.
  - "Thanks for holding, this is Kim, please hold while I pull up your reservation." and "Hello, this is Mark, I'll
    transfer you to baggage." → `hold_message`.
  - Not a regression. Possible fix: let `intro` exempt the callback and transfer rules in the queue phase.
- **BL4/BL6** unchanged.

## Verdict for Delta run 4

**Yes.** Round 13 fixes N1: all 13 negatives have their explicit labels again, and no new false human appeared.
B1 stays fixed (44/44 openers and the disconnect follow-up are candidates). The Delta 3 replay stays at 0 unsafe, the
L2 suite passes (145) and the L1 r27/r39 tests pass (26). Before any trial with a company other than Delta, BL1
(greeting-led hold loops spending questions) and BL3 should be closed.
