# Hermes Line: short report (draft)

Draft by lane L7, round 4, 2026-11-06, for Can and the coordinator. Answers `docs/BRIEF.md` DONE ("what works,
what does not, open risks, and the owner steps still needed"). State of `main` at `bb66bdd`. Every claim names its
evidence. "Live" means a real phone call or a real provider request; "offline" means tests with fakes.

## Live calls

All calls went through Twilio from the plugin's line (Reno NV). Outbound calls showed the owner's
Google Fi number as verified caller ID (`evidence/twilio/04-verify-check.json`). Durations are Twilio's.

| # | When (UTC) | Call SID | Type | Duration | What happened | Evidence |
|---|---|---|---|---|---|---|
| 1 | 2026-11-05 16:42 | `owner call 1` | outbound to the owner's Spectrum phone, ConversationRelay, agent mode | 80 s | Disclosure spoken; first reply in 1.56 s; then about 39 s of silence after the owner's second reply and the owner hung up. Cause: a 45 s cooldown on the live-person question suppressed any answer to an unclear reply (fixed in L1 round 7). | `evidence/trial/call_68921278eab742c6/trial.json`, `docs/lanes/L1.md` section 15 |
| 2 | 2026-11-05 18:52 | `owner call 2` | outbound to the owner, ConversationRelay | 43 s | No silence. First text of Hermes's turns after 2.0, 1.6 and 7.3 s; spoken fillers covered the slow turn. Hermes finished the call and wrote a summary. | `evidence/trial/call_9fda11b071df4fea/trial.json`, `STATUS.md` |
| 3 | 2026-11-05 18:54 | `owner call 3` | inbound: the owner called his Fi number, Fi "Always forward" to the Twilio line | 92 s | Hermes answered with the disclosure and talked. Twilio received the original caller in `From`, no `ForwardedFrom`. The plugin wrongly treated the owner's number as a hidden caller (fixed in L1 round 12). | `evidence/trial/call_c20d76d32e9e47db/trial.json`, `docs/lanes/L1.md` section 21 |
| 4 | 2026-11-05 20:45 | `owner call 4` | outbound to the owner, **Grok Voice** over Twilio Media Streams | 64 s | Grok spoke for Hermes and used three tools: one question to Hermes, the outcome report, the hang-up. The conversation session connected in 1214 ms once the person was detected; `voice_first_audio` logged 490 ms. Grok connected 53 s, hold-path speech-to-text 12.8 s; harness estimate $0.096 of xAI time (not an invoice). Found: the disclosure recorded twice in the transcript (spoken once) and repeated copies of the caller's words; both fixed offline in L5 round 6. No barge-in happened. | `evidence/trial/call_374a992b53c044f9/trial.json` (`grok_evidence`), `docs/lanes/L5.md` section 7 |
| 5 | 2026-11-05 20:49 | `owner call 5` | inbound: the owner called his Fi number, forwarded | 121 s | The owner's number was recognised (`owner_number: true`). Fi's prompt "you need to press 1 to take this call" arrived after Hermes's greeting had already played; Hermes pressed 1. A 2-minute conversation followed with no tools beyond the call; Hermes offered a note five times. Fixed offline in L1 round 16: the greeting waits for the screening prompt, and a note is offered once. No PIN step (0 attempts). | `evidence/trial/call_94909a575a8941db/trial.json`, `docs/lanes/L1.md` section 27 |
| Delta 1 | 2026-11-05 23:21 | `Delta run 1` | outbound to Delta Air Lines reservations (+1 800-221-1212, approved by the owner), owner mode | 68 s | Delta answered with a conversational automated assistant ("I'm not a live person. I'm Delta's automated assistant."). Its questions had no keypad options, so the menu gate sent nothing (correct, fail closed; 3 blocked actions in the event log), and the task finished as failed. The summary named "agent" and "representative" as said, but the transcript has no such Hermes line: they were blocked, never spoken. No hold, no person. Twilio price $0.028. | `evidence/trial/call_b669b105d4774a49/trial.json`, `STATUS.md`, `docs/lanes/L1.md` section 38 |
| Delta 2 | 2026-11-06 01:37 | `Delta run 2` | outbound to Delta reservations, owner mode | 57 s | The bot's self-introduction was cut off ("Thanks for calling. I'm Del"), our live-person question went out over it, and a late model verdict said "person". The owner was rung for 18 s and did not pick up; after the 20 s timeout the fallback question went to the bot, whose "You're welcome. What can no problem." was reported as an answer, after "Thank you, that's very helpful." No hold, no person. Twilio price $0.014; balance afterwards $13.62606. | `evidence/trial/call_2243b7f205194ab5/trial.json`, `STATUS.md`, `docs/lanes/L1.md` section 42 |
| Delta 3 | 2026-11-06 05:56 | none | refused | none | The plugin's quiet-hours gate (21:00 to 08:00 Pacific) refused the call; nothing was dialled. | `evidence/trial/delta3-stdout.jsonl`, `docs/PROGRESS.md` |
| Hilton 1 | 2026-11-08 06:20 | Hilton call 1 | outbound to Hilton Reservations (toll-free, approved by the owner), owner mode | 152 s | Hilton's assistant, then a keypad menu: the menu gate pressed nothing (the menu offered billing). Hilton transferred to an agent by default. **A real agent answered**; Hermes asked "Am I speaking with a live person?" 1.2 s later; the agent said "Yes. Are you a live person too?", then "Hello? Are you a live person too?" and "I'm a live person.". All were judged unclear (the clear-yes rule needed a whole-reply match), the re-ask was refused by the 20 s question spacing, Hermes was silent for 14 s and the agent hung up. No owner ring. The plugin's own summary wrongly said no person was reached. Twilio business-leg price $0.042. | `evidence/trial/call_05b4aa023f374279/trial.json` |
| Hilton 2a | 2026-11-08 07:31 | Hilton call 2 attempt 1 | outbound to Hilton Reservations, owner mode, round-43 code | 173 s | A different Hilton menu: the gate pressed nothing (billing and cancel options) and blocked "Say yes or press 1 to continue on to an agent" as acceptance wording; two "Representative." phrases; Hilton's assistant said goodbye. No person. $0.042. | `evidence/trial/call_b53427ae32364c51/trial.json` |
| Hilton 2 | 2026-11-08 07:35 | Hilton call 2 (the one allowed retry) | outbound to Hilton Reservations, owner mode, round-43 code | 619 s | Same menu as call 1, then a transfer. **Real agents answered** and replied to our question with "Yes. You are speaking with the live person. Yes. Indeed." and similar eight times. The narrowed round-43 rule (yes plus only a question back) judged every reply unclear, Hermes kept asking, and the worker stopped the call with `run.py down`. No owner ring. $0.154. Twilio balance $12.41206 before Hilton call 1, $10.98406 after all three. | `evidence/trial/call_94932d5a13a94ed4/trial.json` |

### What changed after the Delta runs (offline, reviewed)

- **After run 1** (L1 round 25, L2 round 5): a new `automated_assistant` label. On restricted tasks, until a person is
  detected, Hermes says only fixed phrases chosen by rule ("Representative.", "Agent.", "Speak to an agent.",
  "<category> question.", "No, a general question."), at most one every 8 s and 12 per call; data requests get
  "Agent.", and callback, payment and change offers get nothing. No model text is spoken to a bot.
  (`docs/lanes/L1.md` section 38)
- **After run 2** (L1 rounds 30-31, L2 round 9):
  - **Clear yes before any ring:** on restricted tasks the owner is rung only after our fixed live-person question
    is answered with a plain yes; a late or model "person" verdict only triggers the question.
  - **One quiet gate:** every fixed line after the disclosure waits until the business has finished and 1.2 s of
    quiet have passed, with a hard cap at 10 s.
  - **Honest fallback:** the closing line is always "Thank you for your time. Goodbye."; the outcome is
    `unverified` when the business said anything, `unanswered` when it said nothing, and the summary quotes the
    reply with "answer not verified".
  - Replaying run 2 through the current detector gives 0 of 9 "person" verdicts (`STATUS.md`, L2 round 9).
  - (`docs/lanes/L1.md` sections 42-43)
- **Review:** R1b delta 34 says YES for Delta run 3 with this engine and harness (`evidence/reviews/R1-DELTA34.md`).

## Other live evidence

- Twilio caller-ID verification of the Fi number (`evidence/twilio/03-verify-request.json`,
  `evidence/twilio/04-verify-check.json`).
- An SMS code read from the Pixel 10 Pro XL over USB, code masked (`evidence/sms/probe-check.txt`,
  `evidence/sms/probe-inbox.txt`).
- Twilio filtered a one-time-code text sent to the Twilio line (error 30038): codes must come from a carrier phone
  (`STATUS.md`, 2026-11-05).
- One audio-free xAI connection before call 4 (`tests/l6/dry-round6-grok.json`).
- The Desktop pane inside a real Hermes Desktop, sandbox install with sample data
  (`tools/pane-preview/screenshots/desktop/`, `docs/lanes/L3.md` round 5), and replayed in real time with Delta run
  2's recorded data (`tools/pane-preview/screenshots/live/replay-call_2243b7f205194ab5/`, `docs/lanes/L3.md` round 6).
  The replay showed three pane problems (backlog B7-B9).
- `hermes plugins validate --install-deps` passes cold (`tests/l1/evidence/validate.txt`).
- Offline suites: 1247 tests pass (L1 535, L2 130, L3 66, L4 317, L5 88, L6 111), no network. (`docs/release/test-run.txt`).

## Not proven live

- A real hold: neither Delta run got past the automated assistant (`docs/PROGRESS.md` item 2).
- The live-person check on a real person: Hilton calls 1-2 reached a person three times and missed every time.
  Round 43c fixes it offline: an explicit affirmation is a yes with any words after it, unless the reply says what
  bots, menus or recordings say; at most two questions per party, then the owner judges; the connecting line
  discloses the AI within about 4 s; holding lines while the owner rings. Held-out non-human sets: 0 false "person"
  before and after; the human target set 16 of 16 (was 1 of 16) (`tests/l2/round43-before.json`,
  `tests/l2/round43-after.json`, `tests/l1/test_r43_hilton_yes.py`). Not yet re-proven live. The owner stopped
  business test calls; the next live test is the owner playing the business on his own phone.
- A bridge between the owner and a business (the one live ring, Delta run 2, was wrong and unanswered).
- A business calling back and being matched to its task.
- A verification code sent into a live call (every live trial so far ran with codes off); Android 17's code delay
  (the probe found no phone attached, `evidence/sms/otp-delay-20261105T184714Z.json`).
- Owner line mode with the PIN, including the live-leg bridge (`owner_live_bridge`).
- Barge-in and key tones on the Grok path.
- Holds longer than 30 minutes; dropped-call recovery on a real call.
- The pane in the owner's own Hermes Desktop during a live call.

## Known limits

From `docs/BACKLOG.md` (non-gating; Medium/Low findings are fixed in parallel there):

| # | Limit | Status |
|---|---|---|
| B1 | Flaky test `test_R17_child_dials_while_the_owner_stays_on_the_line` (passed 3/3 alone) | open |
| B2 | The hold harness hid the reason when a gate refused (`{"error": "GateRejected"}` only), as on Delta run 3 | fixed (`117d6b0`) |
| B3 | A human agent who answers our live-person question with "Yes, how can I help?" fails closed: the owner is not rung | accepted cost; watch live |
| B4 | Unrestricted calls keep question-back confirmation without a global question budget | accepted |
| B5 | The stall question can be asked to a known bot (within 4 per call) | accepted |
| B6 | After an abandoned partial utterance, the first following reply cannot confirm a person | accepted cost |
| B7 | The pane can miss short states: "A person answered" lasted 1.2 s on Delta run 2 and the pane polls every 5 s | L3 round 7 (1 s poll while live) |
| B8 | The pane's ended headline shows the summary's status line, not the outcome | L1 round 37 |
| B9 | The pane's time strip is wrong on real calls (no phase history) | L1 round 37, then L3 round 7 |
| B10 | Owner line: the keypad confirmation does not identify a replacement goal or prove the read-back was heard (R1b delta 35, Medium) | open |
| B11 | Owner line: a slowly spoken PIN (fragments over seconds) can reach model history; the keypad PIN is the supported path (R1b delta 35, Medium) | open |
| B12 | The pane merges consecutive same-speaker lines into one bubble in an order that reads contradictory | open |

Also from the plugin README:
- Many real phone menus get no reply on purpose: every sentence must match an allowed greeting, hold line or
  option, and money, callback, payment and change wording silences the menu.
- Owner line mode: R1b delta 35 cleared one attended, keypad-only trial in which the owner asks Hermes to call his
  own Fi line; not business calls or a broader allowlist (`evidence/reviews/R1-DELTA35.md`; B10, B11).
- Without the Twilio Auth Token, webhooks rely on a secret path plus a REST check (`docs/lanes/L1.md` section 9).
- US numbers only (geographic and toll-free).
- Codes only from a carrier phone; wireless adb reads take about 3 to 6 s, sometimes 10 s or more (`STATUS.md`).

## Open risks

1. The first real hold and the first human agent are still ahead; detection on real hold audio is unmeasured.
2. The rules that keep Hermes silent on unknown menus may leave a real business unanswered for the whole call.
3. Spend: Twilio balance $13.62606 after Delta run 2 (`STATUS.md`); the coordinator's stop line is $4.00 and the
   hard floor $3.00. xAI: $2 approved; the harness ledger stood at $0.097 after call 4 (`grok_evidence`).
4. The approval for the Delta number expires 2026-11-12 (`STATUS.md`).
5. The adb parser changed for wireless reads (L4 round 8) and needs a review before any live code trial
   (`STATUS.md`).

## Owner steps still needed (Can)

From `docs/OWNER-STEPS.md` and this round:

1. **CALL-GO for Delta run 3**, inside 08:00 to 21:00 Pacific (R1b delta 34 YES; run 3 was refused by quiet hours).
2. **Pixel on USB** at the Studio for the Android 17 code-delay measurement and any code demo.
3. **Owner PIN** (4 to 8 digits), staged by Vezir, if not done yet, and a go for the one attended owner-line trial
   R1b delta 35 cleared (Can asks Hermes to call his own Fi line).
4. **Demo:** the helper's phone number for the allowlist (it must not share its last digits with the other allowed
   numbers), the staged-business label, the "being recorded for a demo" disclosure for the shoot, and about 20
   minutes to record (`docs/demo/storyboard.md`, `docs/demo/shot-list.md`).
5. **Licence and author name:** MIT with the copyright holder's name (`docs/release/admission-check.md` G8).
6. **Publishing, each your decision (Nov 10-11):** the public GitHub repo, the catalog PR (`docs/release/PR.md`,
   `docs/release/catalog-entry.yaml`), the X post tagging @NousResearch, the submission form.
