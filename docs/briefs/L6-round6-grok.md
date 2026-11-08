# L6 round 6 (coordinator): Grok voice transport in the trial harness

`git merge main`. The owner staged the xAI key at `~/.hermes-line/xai_api_key` (one line, 0600) and approved an
xAI live-test budget of at most $2.00 total. Read `docs/lanes/L5.md`, `docs/lanes/L5-live-test-plan.md`,
`docs/adr/0004*`, and how `plugin/line/voice/` gets `XAI_API_KEY` (secret scope).
1. Add `--transport relay|grok` to `call`, `dry` and `listen` (default relay). Grok = Media Streams + the L5 voice
   path, key read once from the file into the plugin's secret scope like the Twilio values; never printed, logged or
   written anywhere else.
2. A persistent xAI spend ledger under the trial root: before each grok run, estimate the worst case for that run
   (max minutes x list price for the realtime session plus STT, from ADR 0004) and refuse if ledger + worst case
   would pass $1.80; after the run, add the measured connected seconds x price. `preflight` prints the ledger.
   Also read xAI's own usage or billing endpoint if a read-only one exists for an API key; if not, say so.
3. `dry --transport grok`: everything up to the provider call with the fake Twilio adapter, plus ONE real xAI
   realtime connect of at most 5 s that sends no audio (proves key, model id and session config; record connect
   latency and the session-created event's field names, masked). Count its cost in the ledger. No STT stream.
4. Evidence for grok runs adds: Grok connected seconds, connect latency on human answer, first-audio latency,
   barge-in events, STT transcript (masked), ledger before/after.
LIMITS as your brief; network now also allows api.x.ai for the single dry connect. No `call`, no `listen`.
DONE: dry grok output (masked), ledger, tests, SHA, exact commands for a grok `call` and `listen`.
