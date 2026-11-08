# Brief S2: Telephony spike (Twilio, Fi, voice loop, hold and human detection)

## PURPOSE
Hermes Line is a Hermes Agent plugin for the Nous plugin hackathon (deadline 2026-11-11 14:00 PT): Hermes phones businesses for the user, navigates the phone menu, waits on hold for 30+ minutes, detects a live human, then bridges the user in or finishes the task itself. Judges score reliability first. This spike picks the telephony design and writes the exact real-call test plan we run once the budget is released.

## TASK
Read `docs/BRIEF.md` (verify sha256 346bbac78d9e7054ca55985ed0181b13449d2b7fa359cb593d870ad9d3eb167d, else stop) and `AGENTS.md`. From official sources (Twilio docs, Google Fi help pages), with URL and access date for every fact, decide and document:
1. Voice loop: Twilio ConversationRelay vs Media Streams with a realtime speech model. Compare latency, barge-in, sending DTMF mid-call, staying silent on hold, cost per minute, and what text/audio the plugin receives. The brain is the user's Hermes agent (text in, text out); pick the most reliable stack for that.
2. Hold and human detection: how to tell hold music, recorded hold messages, IVR prompts, voicemail and a live human apart, over 30+ minutes, cheaply. Cover Twilio AMD (async), transcript-based rules, audio cues, and a hybrid. Say how we measure accuracy without third-party calls (recorded hold simulation).
3. Bridge: ring the owner's phone and join both legs (Conference vs Dial), whisper/announce to the owner, take-over and hang-up, and failure paths (owner does not answer, business hangs up).
4. Caller ID and callbacks: Twilio verified outgoing caller ID flow for the Fi number; whether Google Fi supports forwarding calls to an external number, which kind (always / no answer / busy), and how it is set; if it does not, a design around it.
5. Public endpoint: the most robust way to expose webhooks and WSS for a normal user and for us (e.g. Cloudflare quick tunnel, named tunnel, Tailscale Funnel, ngrok free), with signature validation.
6. Emergency numbers: the block list the plugin must enforce (US and international) and where in the code path it runs.
7. Budget: cost of one local number plus a minute-by-minute test plan that stays inside the $20 total cap, with a 25% reserve.
Write a test plan `docs/spikes/S2-real-call-test-plan.md`: each real call we will place after the budget is released (to Can's own lines only), its purpose, expected minutes and cost, and the evidence it produces (Call SID, duration, transcript). Write an offline hold-simulation fixture plan (recorded audio or transcript streams) we can run with no calls.

## LIMITS
- No phone calls, no Twilio API calls (read-only included), no purchases, no accounts, no API keys.
- Public pages only; no logins.
- Write only `docs/spikes/S2-*.md` and `docs/adr/0003-*.md`. Commit locally on your branch; no push.
- Mark every fact you could not confirm from an official page as UNCONFIRMED.
- Owner decisions: if you need a decision from Can (the owner), do not ask Can and do not write a decision block yourself. Stop at that point and put the question in your final report to the coordinator: the options (A, B, ...), your recommendation and a one-line reason. Only the coordinator posts owner-decision blocks.

## DONE
- `docs/spikes/S2-telephony.md` with answers 1-7, sources and access dates.
- `docs/spikes/S2-real-call-test-plan.md` with a cost table that sums under $15.
- `docs/adr/0003-telephony-stack.md`: chosen stack and rejected options with reasons.
- Final report: commit SHA, the Fi forwarding answer, the chosen voice loop, the owner steps it needs, open risks.
