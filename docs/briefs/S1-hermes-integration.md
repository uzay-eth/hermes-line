# Brief S1: Hermes integration spike (architecture)

## PURPOSE
Hermes Line is a Hermes Agent plugin for the Nous plugin hackathon (deadline 2026-11-11 14:00 PT): Hermes phones businesses for the user, waits on hold, bridges the user in, answers callbacks and reads SMS codes. The call brain must be the user's own Hermes agent, with its memory and tools, at phone-call latency. This spike decides how the plugin lives inside Hermes so the build lanes can start on solid ground.

## TASK
Read `docs/BRIEF.md` (verify sha256 346bbac78d9e7054ca55985ed0181b13449d2b7fa359cb593d870ad9d3eb167d, else stop) and `AGENTS.md`. Then answer, with file:line citations from the Hermes source at `/Users/mac/.hermes/hermes-agent` (Hermes v0.22.5, read only) and the live docs (https://hermes-agent.nousresearch.com/docs, plugin guide, catalog submission page, https://github.com/NousResearch/hermes-agent/blob/main/plugin-catalog/README.md):
1. Plugin surface: `plugin.yaml` fields, tool registration, bundled skill, `requires_env`, `agent.secret_scope.get_secret`, `requires_hermes`, every catalog admission rule, and what `hermes plugins validate --install-deps` checks.
2. Long-lived server: can a plugin run an HTTP + WebSocket server (Twilio webhooks and a ConversationRelay socket) for the life of the Hermes process, in the CLI, the gateway and Desktop? Where does it start and stop?
3. Call brain: the fastest supported way for a plugin to run the user's Hermes agent turn by turn during a live call (streamed text, memory and tools available, mid-call tool calls such as calendar), without overriding core. Measure first-token and full-turn latency on this Mac with an offline fake ConversationRelay client. Use the Hermes default model only if it runs on a subscription route; otherwise measure with a stub and say so.
4. Results back to the user: how the plugin posts a call summary into the chat that started the task, and how it asks the owner for approval (outbound call, OTP release).
5. Desktop pane: the Desktop plugin SDK surface for a live call list, transcript and Take over / Say this / Hang up controls; how the pane gets live updates.
Build a minimal prototype plugin in `spikes/s1-proto/` that registers one no-op tool, starts the server, and passes `hermes plugins validate --install-deps`.

## LIMITS
- No phone calls, no Twilio API calls, no purchases, no new API keys.
- Read Hermes source; do not edit it. Do not change Can's Hermes profiles, config, skills or plugins.
- Install and validate only into a throwaway `HERMES_HOME` under `/Users/mac/.hermes/cache/scratch/hermes-line-s1/`.
- Do not restart any running Hermes gateway.
- Write only under `spikes/s1-proto/`, `docs/spikes/S1-hermes-integration.md` and `docs/adr/0002-*.md`. Commit locally on your branch; no push.
- Use the highest effort level: this is the architecture decision every lane builds on.
- Owner decisions: if you need a decision from Can (the owner), do not ask Can and do not write a decision block yourself. Stop at that point and put the question in your final report to the coordinator: the options (A, B, ...), your recommendation and a one-line reason. Only the coordinator posts owner-decision blocks.

## DONE
- `docs/spikes/S1-hermes-integration.md`: answers 1-5 with citations, measured latency numbers with the command that produced them, and the risks.
- `docs/adr/0002-plugin-architecture.md`: the chosen design and the rejected options with reasons.
- Validate output for `spikes/s1-proto/` pasted in the spike doc.
- Final report: commit SHA, the three most important findings, open questions.
