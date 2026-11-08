# Brief L6: live trial harness (no calls placed by the worker)

## PURPOSE
Hermes Line (Hermes Agent plugin, hackathon deadline 2026-11-11 14:00 PT) has passed its security review for a first live trial: one outbound call from the plugin's Twilio number to the owner's own phone, ConversationRelay transport, OTP off. The coordinator places that call. This lane builds the harness that makes the trial a one-command, repeatable, evidence-producing step, so the call itself costs only minutes.

## TASK
Read `docs/BRIEF.md` (verify sha256 346bbac78d9e7054ca55985ed0181b13449d2b7fa359cb593d870ad9d3eb167d, else stop), `AGENTS.md`, `docs/CONTRACT.md`, `docs/lanes/L1.md` (sections on ACP driver, live runs under `tests/l1/bench/`, webhook auth fallback), `docs/spikes/S2-real-call-test-plan.md`, `evidence/reviews/R1-DELTA2-2976a70.md`.
Build `tools/trial/`:
1. Public endpoint: Cloudflare quick tunnel (`brew install cloudflared` is allowed; free, Apache-2.0, no account). Start it, read the public hostname, health-check HTTPS and WSS through it, stop it cleanly. Document why a quick tunnel is fine for an attended trial and what a normal user should use.
2. Plugin runtime: the plugin service runs from this checkout in a throwaway `HERMES_HOME` under `/Users/mac/.hermes/cache/scratch/hermes-line-trial/`, with config: allowlist exactly `[owner phone (Spectrum)]` and `[owner phone (Fi)]`; owner number `[owner phone (Spectrum)]`; `sms_source: none`; `otp_browser_fill: false`; transport ConversationRelay; ingress auth fallback (no Auth Token); disclosure ON; recording OFF; quiet hours ON. The call brain is the ACP driver attached to the owner's running Hermes gateway, the same way `tests/l1/bench/live_run_acp.sh` attaches (attach only, never start a gateway; refuse if none is ready). Twilio credentials come from `~/.hermes-line/twilio_account_sid` and `~/.hermes-line/twilio_api_key.json` and the line number from `~/.hermes-line/twilio_line_number`, passed in the way the plugin's `requires_env` / secret scope expects in that throwaway home; never printed, logged or written into the repo.
3. `tools/trial/run.py` with subcommands: `preflight` (balance via API, must be >= $5.00, else stop; tunnel up; service ready; allowlist check; prints a masked summary), `dry` (everything up to the provider call, with the fake adapter: proves the TwiML/URLs the real call would use), `call --to <allowlisted> --task "<text>" --mode agent|owner --max-minutes N` (places ONE real call; refuses unless `--i-am-the-coordinator` is given), `evidence <call_id>` (writes `evidence/trial/<call_id>/`: Call SID, duration, status, Twilio price when available, masked transcript, event log, balance before/after), `down`.
4. Hard stops in `call`: max minutes (default 3) enforced by the plugin and by a Twilio `TimeLimit`; balance re-read before dialing; Bora number and anything not on the allowlist refused before any API call.

## LIMITS
- Do not place any call and do not run `call`. Do not change any Twilio number's configuration. Read-only Twilio API use is allowed only for the balance check in `preflight`.
- Network: brew install of cloudflared, the quick tunnel, Twilio balance read. Nothing else.
- Write only `tools/trial/`, `tests/l6/`, `docs/lanes/L6.md`. Commit locally; no push.
- Do not edit plugin code; if the plugin lacks something the harness needs, stop and report the exact missing hook.
- Do not change the owner's Hermes profile; attaching ACP may save one session into it, as in L1's live run.
- Owner decisions: if you need a decision from Can (the owner), do not ask Can and do not write a decision block yourself. Stop at that point and put the question in your final report to the coordinator: the options (A, B, ...), your recommendation and a one-line reason. Only the coordinator posts owner-decision blocks.

## DONE
- `preflight` and `dry` outputs (masked) in `docs/lanes/L6.md`; tunnel stopped afterwards.
- `tests/l6/` output (offline parts).
- Final report: commit SHA, the exact command the coordinator runs for the first call, open risks.
