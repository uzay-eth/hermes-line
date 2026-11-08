# L6 round 8 (coordinator): owner line PIN in the trial harness

`git merge main` (5b654a4: L1 round 13 owner line mode, `HERMES_LINE_OWNER_PIN` as optional_env; read
`docs/lanes/L1.md` latest section and `plugin/README.md`). The owner will stage the PIN at
`~/.hermes-line/owner_pin` (one line, 4-8 digits, 0600) through Vezir's secure path.
1. `listen` loads it once into the plugin's secret scope like the Twilio values when the file exists; never printed,
   logged, written to evidence or the repo; shape check only (digits, length). If absent, owner line mode stays off
   and `preflight` says so.
2. `preflight` shows `owner_pin: present|absent` only.
3. Evidence for a listen run adds: owner_pin_attempts (count, pass/fail, never digits), owner mode entered yes/no,
   tasks created from the call, bridge legs.
4. Tests: the PIN never appears in any evidence or log written by the harness (plant a synthetic PIN and grep all
   outputs), absent file path, bad shape refused.
No `call`/`listen`. LIMITS as before. DONE: tests, SHA, the exact listen command for the owner-line trial.
