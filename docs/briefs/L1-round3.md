# L1 round 3 (coordinator)

First run `git merge main` (your round 2 is merged at 521522f; 105 tests OK there).

## Decision on the driver: B, then the rule
- You may make ONE guarded live ACP run on Can's route: same 14 turns, same credential guard, same `HERMES_SAFE_MODE=1`,
  no retries. The only allowed write to `~/.hermes` is the one session the gateway saves for this run. Give it a title
  that starts with `hermes-line latency test`. Do not delete or edit it afterwards. Report the before/after hashes of
  `config.yaml` and `auth.json` (must match) and the session id that was created.
- Rule on live numbers: if ACP full-turn p50 is at most 1.5x the live embedded p50 (1573 ms, so at most 2360 ms),
  make ACP the default and remove `line/engine/embedded.py` and every private Hermes import from the shipped package,
  so `validate` shows no private-import warning. Keep the driver interface. If ACP is slower than that, keep both,
  keep embedded as the default, and report both numbers; do not delete anything.
- If ACP runs at the profile's reasoning effort, say what it was in the run and note it in `docs/lanes/L1.md`.

## Also
- Tool Search: measure the gain of the briefing change on the finish-tool turn in the same live ACP run if it costs
  no extra turns.

## LIMITS
Same as your brief and round 2. No Twilio calls yet: the coordinator runs the first real calls itself.

## DONE
Live ACP latency table next to the embedded one; hash check; session id; `validate --install-deps` output for the
default you ship; `tests/l1/` output; commit SHA.
