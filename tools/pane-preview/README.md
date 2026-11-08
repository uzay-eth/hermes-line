# Pane preview (offline)

Renders the real `plugin/desktop/plugin.js` against contract-shaped fixture snapshots, at the pane's
registered width (380 px) inside a 1440 px Hermes-Desktop-shaped window. No network, no calls, no
installs.

```sh
node tools/pane-preview/preview.mjs shoot            # every shot -> tools/pane-preview/screenshots/
node tools/pane-preview/preview.mjs shoot on_hold    # one shot
node tools/pane-preview/preview.mjs serve            # build and serve on 127.0.0.1, prints the URL
```

URL parameters: `scene=<fixture id>`, `theme=light|dark`, `mode=ok|loading|error|stale|conflict`.

`shoot round3` writes the shipped direction (C, Signal) cropped to the pane plus a thin strip of app
context in `screenshots/round3/`, and a before/after pair for the person-answered state (needs a local
ImageMagick; skipped with a message otherwise). `screenshots/variants/` is the round 2 comparison of
directions A, B and C; A and B were removed from the plugin and are kept only as those images.

## Inside a real Hermes Desktop

`node tools/pane-preview/desktop-run.mjs [scene ...]` runs the real Hermes Desktop (dev Electron from the
Hermes checkout) in an isolated sandbox under `~/.hermes/cache/scratch/hermes-line-l3/desktop/` (own
HERMES_HOME, Electron userData and HOME; the e2e suite's isolation variables), installs a copy of
`plugin/` there, seeds the call store through L1's Store API (`seed_store.py`), and screenshots the pane
against the plugin's real REST API into `screenshots/desktop/`. No owner profile or app state is touched;
outbound network is blocked by a dead proxy. See docs/lanes/L3.md, round 5.

## On a live trial call

`node tools/pane-preview/live-capture.mjs --store <trial run dir | line.db> [--call call_...]` launches the
same sandboxed Hermes Desktop (its own sandbox: `~/.hermes/cache/scratch/hermes-line-l3/live/`) and mirrors
the trial's call store into it read-only (`live_mirror.py`, SQLite `mode=ro`; only the call's task, call and
transcript rows; numbers masked). It screenshots the 1440 px window every 5 s and on each state change into
`screenshots/live/<label>-<time>/`. Start it about two minutes before dialling (Desktop boot).
`--replay evidence/trial/<call>/trial.json` replays a recorded call in real time instead (`replay_trial.py`).

## What is real and what is faked

| Part | Source |
|---|---|
| Pane code | `plugin/desktop/plugin.js`, unmodified, registered through a fake `ctx` |
| UI kit (`Button`, `Textarea`, `ConfirmDialog`, `EmptyState`, `ErrorState`, `Skeleton`, `Codicon`, `isSubmitEnter`) | the Hermes Desktop app's own source, bundled from the local Hermes checkout |
| Stylesheet, fonts, codicons | the Hermes Desktop build (`apps/desktop/dist/assets/index-*.css`) |
| Theme seeds | the app's default `nous` preset, applied the way `applyTheme()` does (`theme.js`; skips two derived colours the pane does not use) |
| `ctx.rest`, events, OS notifications | `fake-ctx.js`: serves one fixture for `GET /calls`, records POSTs, `mode=conflict` answers POSTs with a 409 |
| Chat and sidebar | empty placeholders with one caption naming the scene and that the data is sample (the real right sidebar has no tab strip above the pane and a collapsed Terminal tab below it) |
| Scrollbars | shown, as in the real app (Playwright's default would hide them and widen the text column) |
| Clock | fixed per scene (`now` in `fixtures/index.json`), time zone America/Los_Angeles |

## Requirements

A Hermes checkout with a desktop build at `HERMES_AGENT_DIR` (default `~/.hermes/hermes-agent`). esbuild,
React, nanostores and Playwright come from its `node_modules`. Playwright uses any cached
`chromium_headless_shell-*` under `~/Library/Caches/ms-playwright`; set `PANE_PREVIEW_CHROMIUM` to
point at another binary. Nothing is downloaded.

## Fixtures

`fixtures/*.json` are `GET /api/plugins/hermes-line/calls` snapshots (`{revision, calls}`) as defined
in `docs/CONTRACT.md`. All of it is **sample data**: businesses are named "Sample ...", numbers are
from the fictional 555-01xx range and arrive masked (`+1 555-01••`), codes are already masked as
`[CODE]` the way the engine sends them. L1 can reuse these files as response fixtures.
