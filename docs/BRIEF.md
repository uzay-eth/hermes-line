# BRIEF: Hermes Line, a real phone line for Hermes Agent (hackathon entry)

Source: Can, Hermes Desktop chat with Vezir, 2026-11-04 (PT). Coordinator: Kodcu. Reports go to Vezir.

## PURPOSE
Nous Research runs the Hermes Agent Plugin Hackathon: 7 days, $20,000 in prizes (1st $10,000). Submissions close **2026-11-11 17:00 EST (14:00 PT)**. Entry = a pull request adding the plugin to the Hermes plugin catalog (https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/catalog-submission), a demo video under 90 seconds on X tagging @NousResearch, and a form. Judges score usefulness, reliability, polish and creativity, and favour plugins that do more than a prompt or a skill could. We scanned all 978 catalog entries: nothing lets Hermes phone a business for the user, sit on hold, bridge the user in when a human answers, take the callback, and read the verification codes that arrive by SMS. That is the gap. Can wants to win it, and he will also use the result himself every day: his own spare mobile line becomes Hermes's phone line. Build it to a standard that wins on reliability and polish, not a demo that only works once.

## TASK
Build a native Hermes plugin, working name `hermes-line` (final name is Can's call later; avoid "Hold for Me", it is a Google Pixel feature name), in `/Users/mac/Projects/hermes-line` (local git; see LIMITS for GitHub).

Core user stories:
1. "Call Delta, change my flight to Friday, bridge me in when a human picks up." Hermes places the call, navigates the phone menu (DTMF and speech), waits on hold for as long as it takes, detects a live human, then either rings the owner's own phone and bridges the two calls, or completes the task itself, as the user chose.
2. "Call the restaurant and book a table for 4 at 7." Hermes holds the conversation itself as the user's own Hermes (its memory and tools are available mid-call, e.g. check the calendar), and posts a summary and outcome to the chat when the call ends.
3. A business calls back on Hermes's number: Hermes answers, knows which open task the callback belongs to, handles it or bridges the owner.
4. A verification code arrives by SMS on the line: Hermes can read it, but only for a task the owner approved, and the code is masked in logs and transcripts.
5. Hermes Desktop pane: live call list and transcript, with Take over / Say this / Hang up controls, and the call summary.

Can's line (the setup we dogfood; the plugin itself must work for any user with their own Twilio account and, optionally, an Android phone):
- Mobile number **[owner phone (Fi)]** stays on Google Fi (a real mobile line, so SMS verification codes keep arriving; Twilio-hosted numbers are flagged VoIP and many services refuse to send codes to them, so do not port it).
- The Fi line runs on an unlocked **Pixel 10 Pro XL** that will sit plugged into the Studio by USB (USB debugging on). SMS is read from that phone. Check first whether `adb shell content query --uri content://sms/inbox` works on this Pixel's Android version as the shell user; if not, use a maintained open-source on-device SMS gateway app with a local API, and document the choice.
- Calls go through Twilio: outbound with **verified outgoing caller ID = [owner phone (Fi)]** (Twilio places a verification call to the Fi line; Can answers it), and callbacks reach Hermes through Fi call forwarding to a Twilio number. Verify whether Google Fi supports call forwarding to an external number and how; if it does not, report and design around it.
- Twilio account: Can's existing account (Trust Hub primary profile is approved, so numbers can be bought). API key is in 1Password item "Twilio Bora API Credentials"; the Account SID is in `/opt/data/cache/twilio-bora/account_sid` on the VPS (ask Vezir if you need it moved). Get secrets only through the proper secret route; never print them.
- Voice loop: choose the stack that is most reliable for low latency and barge-in. Candidates: Twilio ConversationRelay (Twilio does STT/TTS, text over WebSocket to us) or Media Streams with a realtime speech model. The call brain must be the user's Hermes agent. Twilio needs a public HTTPS/WSS endpoint; choose and document a robust way for a normal user (e.g. a tunnel) and for us.

Ship as a catalog-ready plugin: `plugin.yaml` + `__init__.py`, Desktop pane through the Desktop plugin SDK, bundled skill telling the agent how and when to use the tools, README with setup, disclosures (rule 13) and screenshots. It must pass `hermes plugins validate --install-deps` and follow every admission rule (https://github.com/NousResearch/hermes-agent/blob/main/plugin-catalog/README.md): credentials via `requires_env`/`agent.secret_scope.get_secret`, no core overrides, Desktop SDK surface only, no self-updater, no bundled binaries, truthful `requires_hermes`.

Milestones (adjust if you see a better order, say why):
- Nov 5-6: feasibility spikes (SMS read on the Pixel, Fi forwarding, voice loop latency on a real call to Can's own phone), then the core: outbound call, IVR, hold detection, human detection, bridge.
- Nov 7: callbacks, SMS/OTP, Desktop pane.
- Nov 8-9: reliability (hold for 30+ minutes, dropped calls, retries, wrong-number and voicemail handling), polish, tests, validation.
- Nov 10: catalog PR text, README, demo storyboard and a recorded demo cut for Can's review. Nov 11 = buffer.

## LIMITS
- Spend: Twilio total cap **$20** (one local number about $1.15/month plus test minutes). Buy nothing and place no call until Vezir sends the line `TWILIO-GO` in this thread; Can is approving the budget now. No other paid service, API key or subscription without asking.
- Calls: dial only Can's own lines (his Spectrum mobile, which he will give you through Vezir, and the Fi line) until Can approves a named third-party number for a demo. Never dial 911 or any emergency number; the plugin must block emergency numbers too.
- Safety defaults in the plugin: every outbound call needs the user's approval; one call at a time per line; the AI-voice disclosure line at the start of a call is ON by default (configurable); recording OFF by default; quiet hours; OTP codes masked everywhere except the single approved tool result.
- GitHub: keep everything in local git. Creating the public repo, opening the catalog PR, posting the demo on X and submitting the form are Can's decisions; prepare them and stop.
- Do not change Can's Fi account, the Pixel's settings or the Twilio account configuration beyond what this brief names; owner-side steps (Fi eSIM, USB debugging approval, Fi forwarding toggle, answering the caller-ID verification call) go to Can through Vezir as one short list.
- Do not touch the Bora Twilio number or its Studio flow.
- Writers and tests run offline except the explicit Twilio/tunnel/phone integration tests.
- Visual output (Desktop pane, demo cut) is shown to Can before any correction round.

## DONE
- Evidence, not claims: a real call log (Twilio Call SID, duration) of an outbound test call to Can's phone with the transcript; a hold test against a real hold line (a number Can approves) or a recorded hold simulation, stated which; an SMS read test with the code masked in the log; screenshots of the Desktop pane at desktop size; `hermes plugins validate --install-deps` output; test suite output.
- README, catalog entry YAML draft and PR description draft in the repo, not submitted.
- A short report: what works, what does not, open risks, and the owner steps still needed.

## Owner decisions (Karar Kutusu)
When you need a decision from Can (the owner), end your message with exactly one block like this:

```decision
id: <short-slug>
q_tr: <soru, Türkçe, tek satır>
options:
  A: <seçenek, Türkçe>
  B: <seçenek, Türkçe>
recommend: A
why_tr: <tek cümle gerekçe, Türkçe>
blocking: yes
deadline: <YYYY-MM-DD, optional>
```

Keys stay in English; q_tr, the options and why_tr are Turkish, one line each. 2-6 options, keys A-Z or 0-9.
blocking: yes only if you cannot continue without the answer. recommend and deadline are optional.
Always give an id (lower-case letters, digits, . _ -). Re-post the same id to change the question (the old version
is replaced); use a new id for a new question (an identical re-post is ignored). When it is no longer needed, post:

```decision-resolved
id: <short-slug>
```

Then wait: the answer arrives as a message that starts with `OWNER-DECISION K<n>`, for example
`OWNER-DECISION K7 (<short-slug>): A (...). Source: Can via Vezir, telegram message ...`. Never guess the answer and
never put secrets in the block.
