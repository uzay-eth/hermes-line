# L3 round 3 (coordinator): Can picked direction C (Signal)

Can's notes (2026-11-05, via Vezir), the only changes for this round:
1. The solid blue block is heavy and clashes with the red Hang up button.
2. The business's opening line is duplicated in the banner and the transcript.

First run `git merge main` in your worktree. Then:
- Make C the only shipped direction (default and only path in `plugin/desktop/plugin.js`). Remove A and B from the
  shipped plugin; the preview harness may keep them only if they do not ship.
- Fix note 1 and note 2. The person-answered moment must still be the strongest moment in the pane.
- Also fix what you can see in your own C screenshots without changing the direction: the phase strip must be
  legible (labels aligned to their segments); no transcript line hidden under the pinned footer; small grey text
  meets contrast.
Same LIMITS as your brief. Stop after this version.

DONE: light screenshots of on hold, human detected, owner connected, ended; dark of human detected; before/after
pair for the person-answered state at `tools/pane-preview/screenshots/round3/`; detect + Taste preflight result;
tests output; commit SHA.
