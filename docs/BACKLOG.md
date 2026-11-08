# Backlog (non-gating findings and follow-ups)

Rule (Vezir, 2026-11-06): a review blocks only for Critical/High findings, or anything that can ring Can wrongly in a
way that leaks data or commits to something. Medium/Low findings land here and are fixed in parallel. One review
round per change.

| # | Item | Source | Owner | Status |
|---|---|---|---|---|
| B1 | Flaky `test_R17_child_dials_while_the_owner_stays_on_the_line` (passed 3/3 alone) | L1 r35 run | L1 | fixed 3d79184 (hardened, not reproduced) |
| B2 | Hold harness hides the GateRejected reason | Delta run 3 attempt | L6 | fixed 117d6b0 |
| B3 | Human agent that answers our live-person question with "Yes, how can I help?" fails closed (owner not rung) | R1b d28 cost note | L1/L2 | accepted cost; watch live |
| B4 | Unrestricted calls keep question-back confirmation without a global question budget | L1 r33 scoping | L1 | accepted |
| B5 | Stall question can be asked to a known bot (within 4/call) | L1 r35 | L1 | accepted |
| B6 | Abandoned partial: first following reply cannot confirm | L1 r36 | L1 | accepted cost |
| B7 | Pane misses short states ("A person answered" lasted 1.2 s; pane polls every 5 s) | L3 r6 replay | L3 (1 s poll while live), later L1 event forwarding | fixed 6d9b789 (poll); forwarding open |
| B8 | Ended headline shows the log line, not the outcome | L3 r6 replay | L1 (outcome sentence first) | fixed 3d79184 |
| B9 | Time strip wrong on real calls (no phase history) | L3 r6 replay | L1 (explicit phase list per call), then L3 | L1 fixed 3d79184; L3 aliases in r8 |
| B10 | Owner line: keypad confirmation does not identify a replacement goal or prove the read-back was heard | R1b d35 Medium | L1 | open |
| B11 | Owner line: a slowly spoken PIN (fragments over seconds) can reach model history; keypad PIN is the supported path | R1b d35 Medium | L1 | open |
| B12 | Pane merges consecutive same-speaker lines into one bubble in an order that reads contradictory ("Please hold ... Sorry, I didn't catch that") | coordinator check of L3 r6 replay | L3 | fixed 6d9b789 |
| B13 | Can's personal numbers appear in tracked files and history (BRIEF, STATUS, lane docs, evidence/round-latest-*.json, send_latency_probe.py). Before any public repo: scrub tracked files, read numbers from ~/.hermes-line, publish from a fresh history | coordinator check | coordinator + L7 | working tree fixed 543e2fa (scanner 0); history still has them; publishing = fresh history, Can decides |
| B14 | SMS from [the line] is likely blocked without A2P 10DLC registration (error 30034); not checked | L4 r9 | coordinator | open |
| B15 | L2 r10 labels a clear "Yes, I'm a live person." during hold as hold_message (L1 r39 engine guard covers it) | L1 r39 | L2 r11 | fixed 67acb35 |
| B16 | Live trial process must run detached from the agent session (Delta 3 killed by agent_close at 14 min) | Delta run 3 | coordinator | process rule: nohup/setsid + foreground polling until RUN-EXIT |
| B17 | "Would you like to switch channels?" (automated_assistant) draws "Representative." instead of silence | R1b d36 M1 | L1 r40 | in r40 |
| B18 | Recorded greeting lines repeated by a queue ("Thank you for holding...") stay possible_new_party on every repeat; could spend the 12-question cap on another company's queue | R2 d1 BL1 | L2 | open (not Delta) |
| B19 | Affirmatives not on the shared list ("Uh, yes.") during hold -> hold_message, Hermes silent instead of re-asking | R2 d1 BL2 | L2/L1 | fixed fe02425 |
| B20 | A recording that names a person can be labelled human on unrestricted calls without a bot label | R2 d1 BL3 | L2 | open (not Delta) |
| B21 | Recorded-tip word list fitted to Delta; L2 reply window 10 s < live 13.9 s; hold phase ends only on confirmed human | R2 d1 BL4-6 | L2/L1 | open |
| B22 | A named agent whose first line mentions callback/transfer/"please hold" gets a non-person label (not a regression) | R2 d1 BL7 | L2 | open |
| B23 | Flaky test_r13_owner_line N13 success-resets-streak: reads owner_auth_failures count (3 != 4) before the 2nd failure is stored; failed once in full L1 on main (560), passes 3/3 alone | coordinator main run after r41 | L1 | fixed 47e6806 (test waited on in-memory counter) |
