# Review R2 delta 1, re-check: L2 round 12 (fe02425)

Reviewer thread: thr_4vtu5rhu56. One short round, offline only. No calls, network, provider access or `~/.hermes-line/`.
No commits. The repo was only read. Every run below used `git archive` exports in this scratch directory.

## Scope and runs

- Reviewed: `git diff 67acb35..fe02425 -- plugin/line/detect tests/l2 docs/lanes/L2.md`.
- Main is 3b099d8. It has no plugin, tests/l2 or fixtures change after fe02425. Export: `main12/`. The previous main
  (f0871da, rounds 10 + 11) is `main/`. The pre-round-10 baseline is `base/`.

| Run | Result |
|---|---|
| `tests/l2` at 3b099d8 | 144 tests OK, 1.09 s |
| Delta 3 replay (`test_delta_run3`, 7 tests: replay with and without the fake model, held-out, agents after the replay) | 7 OK. 0/25 HUMAN/new-party markers, no model calls during the queue. |
| L1 `test_r27_new_party` + `test_r39_hold_queue` at 3b099d8 (hermes venv) | 26 tests OK, 185.9 s |
| My 22 openers, both queue starts (`probe.py` → `probe-main12.json`, `probe-summary-r12.txt`) | 44/44 are PNP or `human`. None is `hold_message` or `automated_assistant`. |
| Disconnect follow-up and split / org-prefixed lines (`probe2.py`) | Every line is PNP or `human`. "Thank you for your patience, how can I help you?" → "I'm not hearing anything, I'll disconnect now." gives PNP → PNP. |
| New: lines with a greeting word that should be menus, voicemail, offers or hold (`probe3.py`, `probe4.py` → `probe3-out.txt`, `probe4-out.txt`) | **Regressions, below** |

## B1 from the first review: fixed (yes)

- All 22 openers are candidates again, under both queue starts. That covers every line in B1's table, including the
  two identifier requests that were `automated_assistant` and the wait-time, `_recorded_tip` and org-first lines.
- The disconnect follow-up is a candidate.
- Replies to our question: "Yes." and "Yes, I'm a live person." give `human` in the window and PNP when late.
  "Uh, yes.", "Yeah, I'm here.", "I am." and "Yes, how can I help you?" give PNP in the window, so BL2 is fixed there.
  Late (13 s), the first three are still `hold_message` (BL2/BL5 remain).
- No regression on Delta's recorded queue.

## Blocking findings

### N1 (High): the new early person-cue branch overrides voicemail, menu, offer, recorded-message and hold rules, and creates new false HUMANs

Round 12 moved the queue-phase person-cue check to just after `_cut_introduction` in `classify`. It now sits before
the brand-question, voicemail, IVR (`press|dial`), yes/no-offer, callback, transfer, `recorded|recording` and
`please hold|remain on the line|your call is important` rules. Only `_declares_assistant` still wins. The cue also
matches anywhere in the line now (`hello|hi|thank you for holding…|this is <word>|<word> speaking|…`).

Results after "Please hold." or a transfer:

| Line in hold | main f0871da (r11) | main 3b099d8 (r12) |
|---|---|---|
| Hello, this is Mark. I'm not available, please leave a message. | `voicemail` | **`human/high`** |
| Hi, this is Delta. Please leave your name and number after the beep. | `voicemail` | **`human/high`** |
| Hello, this is Kim with Delta, please leave your name and number and I will call you back. | `voicemail` | **`human/high`** |
| Hi, you've reached Dana. Please leave a message after the tone. | `voicemail` | PNP |
| Thank you for calling. For reservations, press 1. / Hello! To hear these options again, press star. | `ivr` | PNP |
| Hi, to receive a callback instead of waiting, press 1. / Hi, we can call you back… Press 1 to accept. | `ivr` | PNP |
| Thanks for waiting. Say yes and we'll text you a link. | `automated_assistant` | PNP |
| This is a recorded message. Please stay on the line. | `hold_message` | PNP |
| Thank you for holding. Please hold for the next available agent. | `hold_message` | PNP |
| Hello, your call is important to us. Please remain on the line. | `hold_message` | PNP |
| Thank you for holding. This call may be recorded for quality assurance. | `hold_message` | PNP |
| You will be speaking with an agent shortly. ("be speaking" matches `<word> speaking`) | `hold_message` | PNP |
| Thanks for calling, I'll transfer you to the next available agent. | `hold_message` | PNP |

Why the three `human/high` results happen: `named_greeting` matches "hello/hi, this is <name>". The new branch then
returns HUMAN when `intro and physical`. Its exclusion list (`recorded|recording|please hold|press|say yes|callback|call
back`) does not include `leave … message`, `name and number`, `beep` or `tone`. That breaks the review's "no new false
humans" check.

Engine effect (read in `machine.py` `_on_detection`; not run end to end):
- **Strict calls** (restrictions, or any earlier bot label, as on Delta): an L2 `human` with no pending question only
  starts our question, so the owner is not rung. But the call loses its voicemail handling and spends a question.
- **Unrestricted calls with no bot label:** `label == "human"` with `_after_question` true (always true before any
  question) goes straight to `_on_human`. **That rings the owner into a voicemail.**

Menus and offers labelled PNP get no menu path (no key), so a callback or language menu in the queue goes unanswered.
Common hold-loop lines labelled PNP each spend an engine question on every repeat. With r39's caps of 4 per segment and
12 per call, a loop like "Thank you for holding. Please hold for the next available agent." can use up the budget
before the agent comes. That is run 3's failure again, and round 12 makes BL1 much wider than before: these lines were
`hold_message` on r11.

Why Delta's replay does not catch it: none of Delta's recorded queue lines (turns 20–41) contains a cue word.

Fix (small, L2 only). This is what B1 asked for: the person cue goes ahead of the *heuristic* rules only (`_recorded_tip`,
the wait-time rule, `_assistant`/identifier patterns), not ahead of the *explicit* ones.
- Keep these before the person-cue branch: voicemail, `press|dial|keypad`, `say yes|no` / `press or say`, callback,
  transfer, `recorded|recording`, `please hold|remain/stay on the line|your call is important|in the queue`.
- Restrict the `<word> speaking` cue to the start of the line or after a name/organisation, not after "be".
- Add the 13 lines above as negative controls (none `human`; voicemail/IVR/offer labels kept) next to the 22 openers.

## Backlog (non-blocking, still open)

- **BL1** as before (greeting-led recordings such as "Thanks for calling Delta Air Lines." and "Hello, and welcome to
  Delta." are PNP 40/40 on repeat). This also covers the part of N1 that remains after the fix above.
- **BL2/BL5:** near-affirmatives that arrive after L2's 10 s window are `hold_message` (in-window they are now PNP).
- **BL3** (pre-existing): "Hi, this is Ed Bastian, thank you for flying Delta." is `human/high`. A bare "Yes." right
  after our question is `human`. On a plain hold, "Hello, who is this?" / "Hello? Is anyone there?" right after our
  question are `human`, the same as r11. These matter on unrestricted calls only.
- **BL4/BL6** unchanged.

## Verdict for Delta run 4

**No.** Round 12 fixes B1: every agent opener and the disconnect follow-up are person candidates, Delta's replay
stays at 0, and the L1 r27/r39 tests pass. But it puts the person cue ahead of the explicit voicemail, menu, offer and
hold rules. That gives new `human/high` labels on voicemail greetings and PNP on common hold and menu lines. On Delta
(a strict call) it would not ring the owner wrongly, but it can spend the question budget and drop menu handling. On
an unrestricted call it rings the owner into a voicemail. Move the branch below the explicit rules, as described in
N1, and add the 13 negative controls. If that is the only change and the 22 openers, the disconnect follow-up and the
Delta replay still pass, my verdict becomes yes.
