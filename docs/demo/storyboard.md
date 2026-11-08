# Demo storyboard: Hermes Line, 87 seconds

Draft by lane L7, round 4, 2026-11-06, for Can's review. Target length 87 s (the hackathon limit is under 90 s,
`docs/BRIEF.md`). Nothing here has been filmed. Facts are from `main` at `0b4143c`. The shot-by-shot evidence and
recording status is in `docs/demo/shot-list.md`.

## The story

**Protagonist:** Can, on the road, with no time to sit on hold.
**Want:** a booking with a business has to move to Friday, and the business only does that by phone.
**Tension:** the hold. Calling means giving up an unknown stretch of the afternoon to hold music, while driving.
**Change:** Can calls his own number, keys a PIN, tells Hermes what he needs and hangs up. Hermes sits through the
hold, a code goes into the call without Hermes ever seeing it, and Can's phone rings only when the business needs
him.

Every phone event in the video happens for real on a live call. If a step fails on the day, cut the shot and
rewrite the voice-over; never fake it.

## Cast and setup (the honest version)

Can may only dial his own lines until he approves a named third-party number (`docs/BRIEF.md` LIMITS). Toll-free
hotlines can be dialled (`docs/lanes/L2.md` round 3), but a real business would have to agree to be filmed and
published, and its hold and menu are out of our control. So the setup stays **staged and labelled** (option A),
with a helper playing the business.

This story needs three phones, so the helper needs **a phone of their own whose number Can approves** and adds to
`policy.allowed_destinations`:

| Role | Device | Number |
|---|---|---|
| Can (owner): calls in from the car, is rung and bridged in | Can's Spectrum phone, hands-free | Spectrum line = `owner_number` (on the allowlist, `STATUS.md` TWILIO-GO) |
| Hermes's line: the number businesses see and text | the Pixel 10 Pro XL on the Fi line, on USB at the studio Mac (`sms_source: adb`) | Fi line, verified caller ID; Fi "Always forward" to the Twilio line, as on live call 3 (`evidence/trial/call_c20d76d32e9e47db/trial.json`) |
| The business | the helper's own phone, on speaker, filmed; plays a hold recording into the call | helper's number (**Can must approve it**) |
| Hermes | Hermes Desktop on the studio Mac, plugin enabled, pane open, ConversationRelay transport | Twilio line |

Why this layout: Can calls his Fi number from Spectrum, Fi forwards it to Twilio, and Twilio passes Spectrum as
`From` (proven on call 3), so the owner is recognised and offered the PIN. The bridge rings Spectrum, so there is no
forwarding loop. The business texts the code to the number it saw, the Fi line; the text stays on the Pixel (call
forwarding does not move texts, `docs/release/fi-forwarding.md`), where the plugin reads it.

Required for the shoot:
- **Disclosure line for a filmed call.** The default outbound line ends "This call is not recorded."
  (`plugin/line/engine/config.py`). Filming makes that false. Set `disclosure_text` for the shoot to "Hi, this is
  Hermes, an AI assistant calling on behalf of {owner}. This call is being recorded for a demo."
- The helper agrees to be filmed and published.
- **Car safety:** film beat 1 parked, or with Can as a passenger. No keypad use while driving.
- `HERMES_LINE_OWNER_PIN` set; `call_profile` set to the restricted profile (`plugin/README.md` setup step 4).
- Inside 08:00 to 21:00 Pacific (quiet hours, `docs/lanes/L2.md`).
- On-screen label for the whole video: "Staged: the business is played by a friend; the hold music is a recording."

## Shots

Times are cumulative. VO = voice-over (Can's own voice recommended). Call audio: beat 1 from Can's phone
(hands-free speaker, recorded in the car); later beats from the helper's phone on speaker, recorded by the camera.

| # | Time | Screen (studio Mac) | Phone / camera | Voice-over or call audio | Caption |
|---|---|---|---|---|---|
| 1 | 0:00-0:17 | none | Car, parked. Can calls his own number on speaker. He keys the PIN (film the keypad from behind: digits not readable) and talks | (Fi's screening prompt, answered by the line, is cut in editing.) Hermes: "Hi, you've reached Can's line. This is Hermes, an AI assistant. If you are Can, key your PIN, then press pound. Otherwise, how can I help?" Tones. Can: "Call [business] and move my booking to Friday. Ring me if they need me." Hermes reads the number back and asks for its digits: the last seven for a number not approved before (the helper's), the last four for one already approved. Can keys them. He hangs up. | "Caller ID is not proof: a PIN on the keypad." then "Nothing is dialled until he keys the number's digits." |
| 2 | 0:17-0:23 | Pane: the new call, Dialing, the menu, "Pressed 2", then "On hold for" with the clock running | none | VO: "That's it. Hermes makes the call." | "Your Twilio account. Your number as caller ID." |
| 3 | 0:23-0:30 | none | Can drives on (passenger-seat or exterior shot) | VO: "It waits on hold so I don't have to." | none |
| 4 | 0:30-0:33 | Pane clock after a cut | none | none | "[real elapsed hold time] later" (the clock's real value) |
| 5 | 0:33-0:42 | Pane flips to "Someone picked up." with the person's words quoted; desktop notification | Helper on speaker | Helper: "Thanks for holding, this is [helper's name]." After a short pause (Hermes waits for 1.2 s of quiet): "Am I speaking with a live person?" Helper: "Yes, this is [helper's name]." | "A person, not a recording." then "It asks before it rings you." |
| 6 | 0:42-0:56 | Pane transcript shows the code request. Can, back at his desk, types `/line allow-code <task> <sender> call`; the prompt names the sender and the call; he clicks **Allow once** and tells Hermes "send it to the call". Pane: "[verification code sent]" chip. | Pixel on the desk lights up with the text (film it dark or from the side: the code must not be readable) | Helper: "I've texted a code to the number on the account. Can you give it to me?" Key tones. Helper: "Got it, you're verified." | "Hermes never sees the code. You approve which call gets it." |
| 7 | 0:56-1:06 | Pane: "Ringing your phone", then "You are on the call" | Spectrum rings on the desk; Can hears the private message, presses 1 | Helper: "To move it I need the account holder." Hermes: "One moment, I'm bringing him on." | "Bridged in only when a person needs you." |
| 8 | 1:06-1:14 | Pane stays on "You are on the call" | Can on the phone | Can: "Hi, it's Can. Friday works." Helper: "Done, see you Friday." | none |
| 9 | 1:14-1:22 | Chat: the call summary Hermes posted (whatever it really says) | none | VO: "One call from the car. Hermes did the waiting." | none |
| 10 | 1:22-1:27 | End card | none | none | "Hermes Line. A phone line for Hermes Agent." |

The call from the car is a task in agent mode ("ring me if they need me"): Hermes handles the business and calls
`hermes_line_call_bring_owner` when the helper asks for the account holder. `_approve` issues the owner bridge grant
in either mode when `owner_number` is set (`plugin/line/engine/service.py`), so shot 7 is possible for a task created
by phone.

The brief requires hold, human detection, owner bridge and code approval in the video. If one of them cannot be done
live, report it as a blocker; do not paper over it.

## What must be recorded live, and what can be screen-captured

**Recorded live (camera or phone audio), on real calls:**
- Beat 1 in the car: the call to the line, keypad tones for the PIN, the read-back, the four confirmation digits
  (Can's phone audio).
- The business call: disclosure, "thanks for holding", the live-person question and answer, the code request, key
  tones, "one moment", Can talking (helper's phone on speaker).
- The Pixel lighting up with the text (shot 6), Spectrum ringing and Can pressing 1 (shot 7).

**Screen-captured on the studio Mac, from the same live calls:**
- The pane in every state (shots 2 to 8), the desktop notification (shot 5), the code approval in chat (shot 6),
  the summary (shot 9).
- The pane must be the plugin running in Can's Hermes Desktop. The sandbox captures in
  `tools/pane-preview/screenshots/desktop/` and the harness renders in `round4/` use sample data; use them only as
  labelled B-roll, never as the live call.

**Made in editing:** the time cut in shot 4 (with the real elapsed time), captions, the end card. Per the global
routing, the end card and captions use HyperFrames, rendered locally.

## Must be true before filming (from the repo evidence at `0b4143c`)

| Needed for | Status now | Evidence |
|---|---|---|
| Beat 1, owner line mode | R1b delta 35: YES only for one attended, keypad-only trial in which Can asks Hermes to call his own Fi line, codes off, two-number allowlist; "not clearance for general business calls". **The demo's call to the helper (a third number) and codes on (shot 6) go beyond that** and need a review check. Medium limits B10 and B11 stay open. Not tried live | `evidence/reviews/R1-DELTA35.md`, `docs/BACKLOG.md`, `docs/lanes/L1.md` sections 22-25 |
| Beat 1, owner recognised on a forwarded call | Live on call 5 (`owner_number: true`); Fi's "press 1 to take this call" prompt was answered by the line | `evidence/trial/call_94909a575a8941db/trial.json` |
| Fi "Always forward" to the Twilio line | Worked live on calls 3 and 5; Twilio got the original caller in `From`, no `ForwardedFrom` | `evidence/trial/call_c20d76d32e9e47db/trial.json`, `evidence/trial/call_94909a575a8941db/trial.json` |
| No silences on the call | Live calls 2, 4 and 5 had none; fillers covered slow turns | `evidence/trial/call_9fda11b071df4fea/trial.json`, `evidence/trial/call_94909a575a8941db/trial.json` |
| Shot 5, human detection and the clear yes | Live: Delta run 2 gave a false "person" from a bot. Fixed offline: on restricted tasks only a plain yes to our question rings the owner. The helper must answer with a plain yes such as "Yes, this is [name]"; "Yes, how can I help?" fails closed (backlog B3) | `evidence/trial/call_2243b7f205194ab5/trial.json`, `docs/lanes/L1.md` sections 42-43, `docs/BACKLOG.md` |
| Shot 5, no talking over the business | One quiet gate (1.2 s) on every fixed line; offline only | `tests/l1/test_r31_quiet_gate.py` |
| A hold of a few minutes on a real call | Not tried live: both Delta runs ended at the automated assistant; run 3 was refused by quiet hours | `evidence/trial/call_b669b105d4774a49/trial.json`, `evidence/trial/delta3-stdout.jsonl` |
| Shot 6, code into a call | Offline tests only; every live trial so far ran with codes off. `line_otp_to_call` is called by the chat's Hermes; the call's own session cannot send a code. The wireless adb parser change still needs a review before a live code trial | `docs/lanes/L4.md`, `docs/lanes/L1.md` section 14, `STATUS.md` |
| Shot 6, the code reaches the Pixel quickly | Android 17 delay not measured | `evidence/sms/otp-delay-20261105T184714Z.json` |
| Shot 7, owner bridge | Not live: the one live ring (Delta run 2) was wrong and unanswered. Offline tests only | `evidence/trial/call_2243b7f205194ab5/trial.json`, `tests/l1/test_flows.py` |
| Shots 2-8, pane in Can's Hermes Desktop | Runs in a sandboxed real Hermes Desktop; replayed with Delta run 2's real data, which showed that "A person answered" (1.2 s on that call) never appeared because the pane polls every 5 s, a status-line headline and a wrong time strip (B7-B9, fixes briefed). Shot 5 depends on B7. `tools/pane-preview/live-capture.mjs` can capture the pane during the real call. Enable it in Capabilities → Plugins, then reload | `docs/lanes/L3.md` rounds 5-6, `docs/BACKLOG.md` |
| The helper's number | Needs Can's approval, an allowlist entry and a review check | `docs/BRIEF.md` LIMITS, `evidence/reviews/R1-DELTA12.md` |

Run one full attended rehearsal of the exact script before the filmed take; each take is two real calls (Can's call
in, then Hermes's call out) on the Twilio balance.

## Post text (for X, Can's decision)

Draft, one sentence plus the tag: "Hermes Line: call your own number, key a PIN, and Hermes phones the business,
waits on hold, passes SMS codes in only when you approve, and rings you when a person needs you. Plugin for
@NousResearch Hermes Agent."
