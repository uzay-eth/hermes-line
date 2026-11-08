# STATUS (coordinator: Kodcu)

Deadline: 2026-11-11 14:00 PT. Gate: no Twilio purchase and no call before `TWILIO-GO` from Vezir.

## Lanes
See docs/briefs/ and the table below (thread ids filled in at spawn).

| Lane | Worker | Thread | State |
|---|---|---|---|
| S1 Hermes integration (architecture) | codex gpt-6-astra xhigh | thr_bx7us5tb57 | done b91b1a3, merged |
| S2 Telephony design + real-call test plan | codex gpt-6.1-sol high | thr_zgjwxkykaq | done a28a9d4, merged |
| S3 Pixel SMS + OTP masking | claude-code claude-sonnet-5-5 high | thr_wj36jfg36n | done 748a409, merged; live probe waits for Pixel |

Start receipts: evidence/start-<thread>.json (model/effort/permission verified, turn started, no errors).
Next: core build lanes (call engine, safety gates, Desktop pane) spawn after S1 ADR 0002 lands; real-call spikes after TWILIO-GO.

## Facts from Vezir (2026-11-04)
- Twilio Account SID: /Users/mac/.hermes-line/twilio_account_sid (outside repo; shape checked AC+32 hex; keep out of git and logs). SK + secret: 1Password "Twilio Bora API Credentials".
- Opus runs on bb claude-code as bare id `claude-opus-5-5` (verified on thr_e44esnupec, 3 completed turns). Use it where it is the better choice; never Fable. Current spike workers stay.
- TWILIO-GO not given yet.

## Coordinator decisions (2026-11-04)
- Validation (S1 question): run `hermes plugins validate --install-deps` ONLINE in a throwaway HERMES_HOME as the named validation check (PyPI is free; brief DONE requires it). Writers and tests stay offline.
- Open risk: Android 17 may delay OTP SMS ~3 h for non-default readers (shell and SMSGate alike). Settled only by `probe.py watch` on the Pixel.

## Build lanes (contract: docs/CONTRACT.md, base 2f85c67)
| Lane | Worker | Thread | State |
|---|---|---|---|
| L1 plugin core + call engine | claude-code claude-opus-5-5 high | thr_adzuzxvfdk | r39 3014fff merged (549 OK): no phrases in hold, segment budget, unsafe offers silent; idle |
| L2 safety gates + hold detection | codex gpt-6.1-sol high | thr_56qvuwnr2c | r13 93c029b merged (145 OK): explicit rules before person cues, 0/68 reviewer negatives; idle |
| L3 Desktop pane v1 (show Can before fixes) | claude-code claude-opus-5-5 high | thr_fydmaaeutf | r9 6cc982e merged (76 OK): ended card trimmed; idle until live data |
| L4 SMS/OTP release + persistence audit | claude-code claude-sonnet-5-5 high | thr_zqpdfgyuza | r9 6eafb79 merged (351 OK): send-and-measure probe (not run); idle |
Review plan: L1 ingress/consent and L4 OTP release get a fresh Sol review on the real diff (auth/secrets triggers).

## TWILIO-GO (Can via Vezir, 2026-11-04 PT)
- Spend from existing Twilio balance only ($15.04 at GO); hard floor $3.00 (Bora number rides on it). Coordinator stop line: balance $4.00, checked via API before and after every call. No top-up, no payment method.
- Dial allowlist: [owner phone (Spectrum)] (Can Spectrum), [owner phone (Fi)] (Fi). Nothing else.
- Pixel: adb authorized (no prompt needed), Android 17 / SDK 37 / CP3A.261005.005, patch 2026-10-05. Fi eSIM active: slot 0, isEmbedded=1, Google Fi, voice+data IN_SERVICE (NR_SA, IWLAN). Second slot empty.
- SMS read via `adb shell content query` as shell WORKS on Android 17 (evidence/sms/probe-check.txt, probe-inbox.txt; bodies masked). Android 17 OTP-delay still unmeasured (needs a fresh code + `probe.py watch`).
- BLOCKED: `op` CLI reports "No accounts configured" (1Password app just updated; CLI integration off or app locked). Owner step.

## Owner updates 2026-11-05
- `pane-direction` answered: C. `twilio-relay-addendum`: Console not reachable (Can lost the account email) -> no Console steps; Media Streams path avoids the addendum if docs confirm.
- Auth Token unavailable: L1 builds the no-token fallback. Grok Voice primary: L5 lane.
- 1Password: `op` from agent shells is blocked by macOS (group container "Operation not permitted"), so integration alone is not enough. Owner one-shot: `python3 tools/owner/stage_twilio_key.py` in Can's own Terminal (tested offline with fake op; prints no value).
| L5 realtime voice (Grok default, pluggable) | claude-code claude-opus-5-5 high | thr_i9hjksb6yi | r6 2364893 merged (88 OK): live Grok findings (disclosure once, STT finals only, barge-in evidence); idle |

## Twilio (2026-11-05)
- Key staged by Vezir at ~/.hermes-line/twilio_api_key.json (0600, dir 0700); shapes checked, values never printed.
- Preflight 08:59:58Z: balance $15.03556; existing numbers: Bora only (untouched); verified caller IDs: none; local price $1.15/mo.
- BOUGHT 09:00:20Z: Reno NV local, last4 [the line] (number SID withheld), voice+SMS, no webhooks set yet. Number stored at ~/.hermes-line/twilio_line_number. evidence/twilio/02-buy.txt.
- Next: caller-ID validation for [owner phone (Fi)] via API (OutgoingCallerIds, CallDelay=60) after Vezir confirms Can is ready.
| R1b reviewer (fresh, read-only) | codex gpt-6-astra high | thr_jjj4tc7kq3 | delta15: NO (N16-A partial parsing, N16-B callback as stay, N16-C sequences, N17-R queued turn) -> L1 r19 strict allowlist |
| R1 security review (read-only) | codex gpt-6-astra high | thr_fqb8kizw5e | deltas 10-14 done (owner line YES at d12; Delta engine NO d13 -> fixed r18; hold harness NO d14 -> L6 r11); delta15 FAILED (provider cyber filter, context 157k); replaced by thr_jjj4tc7kq3 |

## R1 security review (codex gpt-6-astra high, thr_fqb8kizw5e) on 202349c: NOT READY
- H1 retained OTP slot crosses replacement approval (L4); H2 fallback REST cache SID-only + historical inbound replay (L1).
- M1 OTP approval destination re-resolved at confirm (L1+L4); M2 Connect/answer replay burns recovery budget (L1); M3 Desktop revision not rechecked at execute (L1).
- Coordinator dispositions: first live trial is call-only with OTP disabled; fallback ingress (no Auth Token) allowed after H2/M2 fixed; browser OTP fill behind setting default OFF; delta review by the same reviewer on the fix diff. Reviewer's L1/L4 test failures were its sandbox (no temp dir); coordinator runs on main pass.
- main dae1b78 tests: l1 150 OK, l4 266 OK, l2 48 OK, l5 70/71 (rehearsal conference test fails after L1 r4; L1 r5 diagnoses). evidence/main-tests/
- Trial OTP-off setting: sms_source: none (per R1 delta), plus otp_browser_fill: false.
| L6 live trial harness (no calls by worker) | codex gpt-6.1-sol high | thr_y57sdtb67b | B13 543e2fa merged: numbers read from ~/.hermes-line, scanner 0 files, BRIEF sha b6ded02c; idle |
- Backlog (Low, R1 delta3): MCP request already authenticated survives token rotation unless call ends; fix before catalog submission.
- First call ready: tools/trial/run.py call --to Spectrum, agent mode, 3 min cap; waits for Can's window after 08:00 PT (quiet hours).
- 2026-11-05 16:42Z FIRST LIVE CALL (owner call 1), from Fi (verified caller ID), to Spectrum, 80 s, completed. Disclosure + 1 Hermes reply (1.56 s). Then ~39 s silence after owner's 2nd utterance (driver.ask stuck, machine.py:1030), owner hung up. Relay accepted: no AI/ML addendum rejection. Twilio notification 15003.
| L7 release texts, Fi steps, storyboard | claude-code claude-opus-5-5 high | thr_78zj2vwjpt | r4 840b59b merged: README r4 patch applied on main, PR, catalog, storyboard, shot-list (10 shots, 87 s), REPORT draft; idle |
- main 44007c0: l1 199/201 (2 r7 tests assume old L2 detector verdict; fix queued in L1 r8), l2 74, l4 275, l5 71, l6 19 OK
- Call profile hermes-line-calls: mcp_servers emptied by coordinator (backup config.yaml.pre-mcp-off.bak in the profile); dry re-run passes (tests/l6/dry-round4-mcp-off.json). plugin/README patched (G10), root synced.
- Call profile: agent.disabled_toolsets [jev, x_search] added by coordinator (backup config.yaml.pre-disabled-toolsets.bak); dry passes with full-scope guard: Enabled toolsets = only this call's hl_ server (tests/l6/dry-round5-pass.json).
- Backlog (Low, R1 delta6) N8: FIXED da80dbc.
- 2026-11-05 Vezir: xAI key staged (~/.hermes-line/xai_api_key, 600, shape ok, not printed), xAI budget max $2 approved; Fi forwarding number added+verified (Don't forward); Twilio 30038 filters OTP texts to [the line] -> codes only from Pixel; [the line] webhooks empty, harness owns it.
- 2026-11-05 18:52Z call 2 (owner call 2) outbound Relay 43 s completed; turns first text 2.0/1.6/7.3 s, fillers covered; no silence. 18:54Z call 3 (owner call 3) inbound via Fi Always forward 92 s completed; From = original caller, no ForwardedFrom; owner not recognised (caller_hidden mislabel); finish turns 5.7-7.3 s. [the line] webhooks restored empty. Balance 13.78356.
- Scope addition (Vezir 2026-11-05): owner line mode with PIN -> L1 r13 after r12.
- Main 5b654a4 suites: L1 258 (1 timing flake test_barge_in_drops_stale_chunks_and_reconciles under full-suite load; passes 5/5 alone), L2 74, L4 288, L5 79, L6 54 OK.
- 2026-11-05 Can via Vezir: Grok CALL-GO; LISTEN-GO; hold-line-candidate = A (Delta +1 800-221-1212, max 45 min hold, bridge Spectrum, 20 s owner timeout then one bag-fee question, never invent booking/identity); panel design approved. Approval stored in ~/.hermes-line/approved_destinations.json (expires 2026-11-12).
- Vezir protocol for Delta run: post DELTA-START (call placed), HUMAN-ON-LINE (human detected, before owner ring), DELTA-END (outcome), each alone. L6 r10 emits live JSON events for this.
- Backlog: L4 r8 16dc2ba changed the adb parser (length-prefixed rows) -> R1 review before any OTP live trial. Wi-Fi read median ~5 s, max ~14 s (phone-side content query); USB recommended.
- 2026-11-05 23:21Z Delta run 1, 68 s: Delta conversational AI assistant; gate silent (correct, fail closed); model finished failed at 70 s; no human; $0.028. -> L1 r25 speech IVR phrases, L2 r5 automated_assistant label.
- BLOCKER for Delta retry: L2 r5 detector labels human agent greetings ('this is Dana, how can I help') as automated_assistant (coordinator repro) -> L2 r6 urgent.
- 2026-11-06 01:36:59Z Delta run 2, 57 s: false human (bot self-intro cut off, slow path human high late) -> owner rung 18 s, no pickup, fallback question to bot, closing line wrong. Balance 13.62606. -> L1 r30, L2 r9. No further retry without new GO.
- R1b delta 34: YES for Delta run 3 (engine+harness). Waiting for CALL-GO.
- R1b delta 35: YES for one attended keypad-only owner-to-Fi trial; B10, B11 backlog. Waiting: owner_pin file + CALL-GO.
- 2026-11-06 16:15:56Z Delta run 3 retry, 843 s: real hold queue reached (23 min announced), killed by agent_close at 14m03s (service_restart hangup); D2 hold announcements possible_new_party (L2 r10 fixed 44e1518), D3 assistant phrases into hold + budget spent (L1 r39 running). $0.21.
