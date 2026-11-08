# L3 round 4 (coordinator): owner-side review notes on direction C

`git merge main`. Review of `tools/pane-preview/screenshots/round3/human_detected-before-after.png`: the direction is
right. Fix these three before the demo, nothing else:
1. A transcript line is cut in half at the bottom edge. End the scroll area with a clean fade, or show only full rows.
2. Secondary grey text and the grey progress segments on the pale blue card are too light. Raise them to WCAG AA
   (4.5:1 for text, 3:1 for the segments) against the card colour itself, in light and dark.
3. The sample data timeline does not add up (header 9:01; Hermes reply in the same minute as the greeting). Make one
   coherent timeline for all states: call start, hold length, human answer time, reply times a few seconds apart,
   owner join, end. The header, time strip and transcript times must agree with each other in every state.
Re-shoot the same states as round 3 into `tools/pane-preview/screenshots/round4/`, plus a before/after of the
human-detected state. Re-run Detect and the contrast checks on the card colours. Same LIMITS as your brief.
Stop after the screenshots. DONE: screenshot paths, contrast numbers, test output, commit SHA.
