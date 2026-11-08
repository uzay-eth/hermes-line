# L3 round 5 (coordinator): the pane inside a real Hermes Desktop

`git merge main`. Before the demo the pane must be seen running inside Hermes Desktop on this Mac, not only in the
preview harness. Find out (read the Desktop plugin SDK docs and the installed Hermes source) how to load
`plugin/desktop/` into Hermes Desktop for one session without changing the owner's default profile or the call
profile `hermes-line-calls` (which must keep zero plugins). Options may include a separate throwaway profile, a
dev-mode loader, or a local catalog install into a scratch profile. Report the options with a recommendation.
If one option needs no owner action and touches no existing profile, do it: load the pane against the plugin's REST
API in fake-adapter mode, take 1440 px screenshots of on hold, human detected, owner connected and ended into
`tools/pane-preview/screenshots/desktop/`, and compare them with round 4 (any visual drift = defect). Stop after the
screenshots. Same LIMITS. DONE: options, screenshot paths, SHA.
