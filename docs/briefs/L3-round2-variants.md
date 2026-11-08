# L3 round 2: three directions before Can sees anything (coordinator)

Can has NOT seen v1. The coordinator reviewed the v1 screenshots and will not show them yet: the pane is clean and
native, but it reads as a generic tool, and it is the centre of the demo judges will watch. Can's bar for a surface
he looks at daily: "clean but a nice internal tool" fails; he expects a bold concept.

## TASK
First run `git merge main` in your worktree (main now has `docs/CONTRACT.md` "Desktop contract clarifications":
`state_since`, `mode` = plan, snapshot revision, ISO 8601, mask tokens `[CODE]/[CARD]/[NUMBER]`). Apply them.
Then build three clearly different directions of the pane, each complete for the states on hold, human detected,
owner connected and ended with summary:
- A: v1 refined. Keep the native, quiet direction; fix the issues below.
- B: the call as a story. The call's phases (menu, hold, person, outcome) are the structure of the pane, with time
  spent in each; the transcript lives inside its phase and old phases collapse.
- C: your own boldest direction that still uses the SDK, the app's theme and native components, and still reads well
  at 380 px. Say in one line what makes it different.
Issues every direction fixes: the primary action follows the state (when the owner's phone is about to ring, the
primary action is not Take over); Hang up reads as destructive; no unlabelled or competing clocks; no repeated
labels (title, "On hold", "Call ended"); Hermes lines are not dimmer than business lines; the newest line is easy to
find; bottom padding.
Keep all three as selectable variants in the preview harness (one code path per variant is fine; only one ships later).

## LIMITS
- Same LIMITS as your brief (SDK surface only, no Pause/Resume or play/stop buttons, sample data labelled, no network,
  no new dependencies, your paths only, local commit).
- Screenshots for Can crop to the pane plus a thin strip of app context; no "not rendered by the preview" text visible.
- Re-run impeccable `detect` and the Taste preflight on each direction.

## DONE
- Per direction: light screenshots of the four states, plus one dark screenshot of "human detected".
- One side-by-side sheet per state (A | B | C) at `tools/pane-preview/screenshots/variants/<state>.png`.
- Your honest one-paragraph critique of each direction and which one you would ship.
- Final report: commit SHA, sheet paths, test count.
