# Brief L7: release texts, Fi steps, demo storyboard

## PURPOSE
Hermes Line is a Hermes Agent plugin for the Nous plugin hackathon (deadline 2026-11-11 14:00 PT). It phones businesses for the user, waits on hold, brings the user in when a person answers, takes callbacks and releases SMS codes only with approval. The judges and catalog reviewers read the README and PR first, and the 90-second demo decides a lot; Can (the owner) reviews all of it before anything is public.

## TASK
Read `docs/BRIEF.md` (verify sha256 346bbac78d9e7054ca55985ed0181b13449d2b7fa359cb593d870ad9d3eb167d, else stop), `AGENTS.md`, `STATUS.md`, `docs/CONTRACT.md`, the lane docs `docs/lanes/L1.md`..`L6.md`, ADRs `docs/adr/`, `plugin/plugin.yaml`, `evidence/trial/` (first live call), `evidence/reviews/`.
1. `README.md` (repo root, also the plugin's README per the catalog rules): what it does in plain words, setup (Twilio account and number, verified caller ID, API key via `requires_env`, public URL, optional xAI key, optional Android phone for SMS), the settings, disclosures (rule 13 of the catalog admission rules: AI disclosure, recording off by default, allowlist, emergency numbers blocked, consent for codes), costs per call (ADR 0004), limits, screenshots from `tools/pane-preview/screenshots/round3/`. Only claim what the repo evidence shows; mark untested items as such.
2. `docs/release/catalog-entry.yaml` and `docs/release/PR.md`: follow the current format at https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/catalog-submission and https://github.com/NousResearch/hermes-agent/blob/main/plugin-catalog/README.md (read both live; quote the rules you follow). Check every admission rule against the repo and list pass / gap in `docs/release/admission-check.md`.
3. `docs/release/fi-forwarding.md`: the exact Google Fi steps for the owner to forward all calls from the Fi line to the plugin's Twilio number, from Google's current official help pages (cite URLs), and how to undo it. Note what forwarding does to SMS (must stay on the phone) and to caller ID of forwarded calls. If Fi only supports some forwarding modes, say which.
4. `docs/demo/storyboard.md`: under 90 seconds, shot by shot (screen, phone, voice-over lines, captions), one real story with a protagonist, a tension (the hold) and a change; it must show the hold, the human detection, the owner bridge, and the SMS code approval. List what must be recorded live and what can be screen-captured.

## LIMITS
- Write only `README.md`, `docs/release/`, `docs/demo/`. Commit locally; no push.
- Network: reading public web pages only. Do not create accounts, repos, PRs or posts.
- Do not place calls or touch Twilio or the phone.
- Plain, concrete English; no marketing filler, no em dashes, no invented numbers. Each number cites its evidence file.
- Owner decisions: do not ask Can. Put open questions in your final report with options and a recommendation.

## DONE
- The files above, with the admission check table.
- Final report: commit SHA, admission gaps, open questions.
