# Demo shot list: Hermes Line (87 s)

Lane L7, round 4, 2026-11-06. Companion to `docs/demo/storyboard.md` (story, cast, script). For each shot: what is
on screen, which real evidence in this repository shows the feature has worked so far, its length, and whether it
still needs a live recording. Evidence is what exists today at `main` `0b4143c`; "offline" means tests with fakes,
never a call. Nothing here has been filmed, and no shot may be faked: a shot whose feature fails on the day is cut.

Recording status key:
- **Live, to record:** must be filmed on the real demo calls; no usable footage exists yet.
- **Screen capture of the live call:** the Mac screen during the same real call; no footage exists yet.
- **Edit:** made in editing (captions, cuts, end card), no recording.

| # | Time | Length | On screen | Backed by (real evidence so far) | Feature proven live? | Recording status |
|---|---|---|---|---|---|---|
| 1 | 0:00-0:17 | 17 s | Car, parked. Can calls his own Fi number, keys the PIN (keypad filmed from behind), asks for the call, keys the number's digits after the read-back, hangs up. Caption: "Caller ID is not proof: a PIN on the keypad." | Forwarding and owner recognition: `evidence/trial/call_94909a575a8941db/trial.json` (call 5: Fi forward, owner's number recognised, screening prompt answered). PIN, read-back and digit confirmation: offline only (`tests/l1/test_r13_owner_line.py`, `tests/l1/test_r16_hold_trial.py`); review `evidence/reviews/R1-DELTA35.md` (one trial calling his own Fi line only) | Forwarding: yes (calls 3, 5). PIN and confirmation: **no** | Live, to record (Can's phone audio and camera) |
| 2 | 0:17-0:23 | 6 s | Pane: the new call, Dialing, the menu, "Pressed 2", then "On hold for" with the clock running | Pane in a real Hermes Desktop with sample data: `tools/pane-preview/screenshots/desktop/on_hold.png`. Outbound dialling with verified caller ID: `evidence/trial/call_9fda11b071df4fea/trial.json`. Keypad menu: offline only (`tests/l1/test_flows.py`) | Dialling: yes. Menu keys on a real menu: **no** | Screen capture of the live call |
| 3 | 0:23-0:30 | 7 s | Can drives on (passenger seat or exterior) | none needed (B-roll) | n/a | Live, to record (camera) |
| 4 | 0:30-0:33 | 3 s | Pane clock after a cut; caption with the real elapsed hold time | Long hold: offline only (40-minute virtual hold, `docs/lanes/L5.md` section 4). Both Delta runs ended at the automated assistant, no hold (`evidence/trial/call_b669b105d4774a49/trial.json`, `evidence/trial/call_2243b7f205194ab5/trial.json`) | **no** | Screen capture of the live call; the cut and caption are edit |
| 5 | 0:33-0:42 | 9 s | Pane flips to "Someone picked up." with the helper's words (needs backlog B7: on the Delta run 2 replay this state lasted 1.2 s and never appeared); desktop notification. Hermes waits for quiet, asks "Am I speaking with a live person?", helper: "Yes, this is [name]." | Live-person question spoken live: call 1 and Delta runs 1-2. Clear-yes rule and quiet gate: offline only (`tests/l1/test_r30_delta_run2.py`, `tests/l1/test_r31_quiet_gate.py`). Live failure that led to them: `evidence/trial/call_2243b7f205194ab5/trial.json` | Question: yes. Correct "person" verdict at a business and the clear-yes ring rule: **no** | Live, to record (helper's phone on speaker) plus screen capture |
| 6 | 0:42-0:56 | 14 s | Can at his desk: `/line allow-code <task> <sender> call`, **Allow once**, "send it to the call"; Pixel lights up (code not readable); key tones; pane shows "[verification code sent]" | SMS read with the code masked: `evidence/sms/probe-check.txt`, `evidence/sms/probe-inbox.txt`. Sealed delivery into a call: offline only (`docs/lanes/L4.md` sections 2 and 5) | Reading a code: yes. Sending a code into a live call: **no** | Live, to record (camera on the Pixel) plus screen capture |
| 7 | 0:56-1:06 | 10 s | Pane "Ringing your phone", then "You are on the call"; Spectrum rings, Can presses 1 | One live owner ring, wrongly triggered and unanswered: `evidence/trial/call_2243b7f205194ab5/trial.json`. Bridge: offline only (`tests/l1/test_flows.py`, `tests/l1/test_r17_live_bridge.py`) | Ring: yes (wrong one). Bridge: **no** | Live, to record (camera on the Spectrum phone) plus screen capture |
| 8 | 1:06-1:14 | 8 s | Can talks to the helper; pane stays on "You are on the call" | Bridge: offline only, as above | **no** | Live, to record |
| 9 | 1:14-1:22 | 8 s | Chat: the summary Hermes posted for this call | Summaries posted on live calls 2 to 5 and both Delta runs (`summary` in each `evidence/trial/*/trial.json`) | yes | Screen capture of the live call |
| 10 | 1:22-1:27 | 5 s | End card: "Hermes Line. A phone line for Hermes Agent." | none needed | n/a | Edit |

Total: 87 s.

## What exists today, usable now

- Pane stills from a real Hermes Desktop with sample data (`tools/pane-preview/screenshots/desktop/`) and the harness
  renders (`tools/pane-preview/screenshots/round4/`). Allowed only as labelled B-roll ("sample data"), never as the
  live call.
- A real-time replay of Delta run 2's recorded data in the pane
  (`tools/pane-preview/screenshots/live/replay-call_2243b7f205194ab5/`). It is a replay of a failed call, not demo
  footage. The capture tool for the real call is `tools/pane-preview/live-capture.mjs` (`docs/lanes/L3.md` round 6).
- No call audio was recorded on any live call (the plugin never records). Transcripts exist in each `trial.json`
  but are not footage.

## Live recordings still needed

Every shot except 3 (B-roll) and 10 (end card) needs footage from the real demo calls: 1, 2, 4, 5, 6, 7, 8 and 9.
Shots 1, 4, 5, 6, 7 and 8 also depend on features that have not yet worked on a live call (PIN and digit
confirmation, a real hold, a correct "person" verdict with the clear-yes rule, a code into a call, the bridge). The
prerequisites and reviews each needs are listed in `docs/demo/storyboard.md`, "Must be true before filming". Run one
full attended rehearsal before the filmed take.
