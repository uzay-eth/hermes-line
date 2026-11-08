# Brief L3: Desktop pane (first version)

## PURPOSE
Hermes Line is a Hermes Agent plugin for the Nous plugin hackathon (deadline 2026-11-11 14:00 PT): Hermes phones businesses for the user, waits on hold and bridges the user in. The Desktop pane is where the user watches a live call and steps in, and it is the centre of the demo video judges will see. It must feel calm and trustworthy during a 40-minute hold and fast when a human picks up.

## TASK
Read `docs/BRIEF.md` (verify sha256 346bbac78d9e7054ca55985ed0181b13449d2b7fa359cb593d870ad9d3eb167d, else stop), `AGENTS.md`, `docs/CONTRACT.md` (Desktop REST and events), ADR 0002 item 9 and `docs/spikes/S1-hermes-integration.md` section 5.
Scope: a Hermes Desktop pane, app UI (not a landing page). Route: new page. Read before any edit, in order:
  1. `/Users/mac/.claude/skills/impeccable/SKILL.md` (run its `scripts/impeccable context` once from the project root)
  2. `/Users/mac/.claude/skills/design-taste-frontend/SKILL.md`
  3. `/Users/mac/.claude/skills/emil-design-eng/SKILL.md`
  4. `/Users/mac/.claude/skills/full-output-enforcement/SKILL.md`
First message: `LOADED: <names>; UNAVAILABLE: <names or none>`.
Build `plugin/desktop/plugin.js` with the Desktop plugin SDK only: live call list, the masked transcript, the current state (hold timer, "human detected", owner ringing), Take over / Say this / Hang up controls, and the call summary. Build `tools/pane-preview/`, an offline harness that renders the pane against fixture snapshots from the contract for every state.
Produce the first version, then stop.

## LIMITS
- Desktop SDK surface only: no renderer patches, eval, private imports, script injection or core DOM manipulation.
- No Pause/Resume or play/stop motion buttons. No template-flat layout. Motion follows real state changes, and respects `prefers-reduced-motion`.
- Sample data in fixtures is labelled sample (555-01xx numbers, "Sample Airline").
- No network, no calls, no new dependencies unless the brief allows them; none are allowed here.
- Write only `plugin/desktop/`, `tools/pane-preview/`, `tests/l3/` and `docs/lanes/L3.md`. Commit locally on your branch; no push.
- Owner decisions: if you need a decision from Can (the owner), do not ask Can and do not write a decision block yourself. Stop at that point and put the question in your final report to the coordinator: the options (A, B, ...), your recommendation and a one-line reason. Only the coordinator posts owner-decision blocks.

## DONE
- Screenshots at desktop size (pane at its real width inside a 1440 px window) for: on hold, human detected / owner ringing, owner connected, agent talking, ended with summary, failed. Paths listed in `docs/lanes/L3.md`.
- impeccable critique/audit/polish and `detect` summary line (or `GAP: ... NOT RUN`); Taste preflight result.
- Final report: commit SHA, screenshot paths, your own honest critique of the render.
