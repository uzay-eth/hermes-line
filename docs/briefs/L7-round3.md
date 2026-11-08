# L7 round 3 (coordinator): release texts up to date with main

`git merge main`. Bring the release texts in line with what main does now and what was proven live. Facts to add:
live calls 2 (outbound, `owner call 2`, 43 s, no silence, fillers) and 3 (inbound via Fi Always forward, `owner call 3`,
92 s; Fi sends the original caller in From, no ForwardedFrom) in `STATUS.md` and `evidence/trial/`; the Grok
Media Streams transport (L5, `--transport grok` in the harness, R1 delta 9 YES, dry connect only so far); owner line
mode (L1 rounds 13-15: keypad PIN, per-line lockout, keypad confirmation of the destination's last four digits;
still under review, mark it as such); Twilio numbers cannot receive OTP texts (error 30038), codes only from the
carrier phone; the pane running inside a real Hermes Desktop (`tools/pane-preview/screenshots/desktop/`); round 4
re-shot. Keep every live/offline label honest and cite the evidence file for each claim.
Files: `plugin/README.md` changes go as a patch file again (`docs/release/plugin-readme.patch`), root README via
`sync_readme.py`; `docs/release/{catalog-entry.yaml,PR.md,admission-check.md}`; `docs/demo/storyboard.md` (beat 1 is
now the owner calling Hermes from the road, keying the PIN, asking it to call a business, and being bridged in;
keep the 90 s limit). Also draft `docs/release/REPORT.md`: what works, what does not, open risks, owner steps.
Same LIMITS. DONE: files, test run record, SHA.
