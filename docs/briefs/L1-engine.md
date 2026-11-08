# Brief L1: Plugin core and call engine

## PURPOSE
Hermes Line is a Hermes Agent plugin for the Nous plugin hackathon (deadline 2026-11-11 14:00 PT): Hermes phones a business for the user, navigates the menu, waits on hold, detects a human, then bridges the owner in or finishes the task itself, and answers callbacks. Judges score reliability and polish first. This lane builds the plugin core every other lane plugs into.

## TASK
Read `docs/BRIEF.md` (verify sha256 346bbac78d9e7054ca55985ed0181b13449d2b7fa359cb593d870ad9d3eb167d, else stop), `AGENTS.md`, `docs/CONTRACT.md`, ADR 0002, ADR 0003, `docs/spikes/S1-hermes-integration.md`, `docs/spikes/S2-telephony.md` and `spikes/s1-proto/`.
Build in your contract paths:
1. Native plugin package (`plugin/plugin.yaml`, `plugin/__init__.py`, bundled skill) per ADR 0002, with tools for: request a call (needs owner consent), call status, say/hang up/take over, list open tasks, and the SMS code tool hook for L4.
2. Profile-owned call service with single-owner lease, clean start/stop, crash recovery, and the task/event store.
3. Authenticated ingress: Twilio voice webhooks and the ConversationRelay WebSocket, signature-validated, call-ID correlated, bounded queues.
4. Hermes call driver (`start / turn / interrupt / close`), streamed, barge-in safe, history reconciled to what was actually spoken.
5. Call state machine: outbound dial with the AI disclosure line, IVR via DTMF and speech, hold, human detected, ring the owner and conference bridge, agent-completes mode, callbacks matched to open tasks, summary posted to the originating chat, voicemail, no-answer, business hang-up and dropped-call paths.
6. Twilio adapter behind an interface with a fake used by all tests; Desktop REST and events per the contract.
Stub L2 and L4 interfaces exactly as the contract names them.
Run one real-model latency measurement through the user's current Hermes model route (at most 20 turns, offline fake relay, no phone) and record first-text and full-turn numbers.

## LIMITS
- No phone calls, no Twilio API calls, no purchases, no tunnels, no new API keys.
- Write only your contract paths, `tests/l1/` and `docs/lanes/L1.md`. Commit locally on your branch; no push.
- Do not edit Hermes source or Can's Hermes profiles. Install and validate only into a throwaway `HERMES_HOME` under `/Users/mac/.hermes/cache/scratch/hermes-line-l1/`. Online access is allowed only for `hermes plugins validate --install-deps`.
- Do not restart any running Hermes gateway.
- Secrets come only through `requires_env` / `agent.secret_scope.get_secret`. Never print or log one. The Account SID file at `/Users/mac/.hermes-line/twilio_account_sid` stays out of git and logs; tests do not read it.
- Every call path goes through `check_destination` and consent before the adapter. A test asserts zero adapter calls for each rejected case.
- Tests run offline.
- Owner decisions: if you need a decision from Can (the owner), do not ask Can and do not write a decision block yourself. Stop at that point and put the question in your final report to the coordinator: the options (A, B, ...), your recommendation and a one-line reason. Only the coordinator posts owner-decision blocks.

## DONE
- Test suite output for `tests/l1/`, covering each state-machine path above with the fake adapter.
- `hermes plugins validate --install-deps` output for `plugin/` in the throwaway home.
- Real-model latency numbers with the command that produced them.
- `docs/lanes/L1.md`: what works, what is stubbed, what needs a real call.
- Final report: commit SHA, test counts, open risks.
