# L1 round 2 (coordinator)

First run `git merge main` in your worktree (main has your L1 merged plus L3 variants and the status updates).

## Decisions on your questions
- Real-model latency: option A, with guards. Run `tests/l1/bench/latency_real_model.py` once with
  `HERMES_HOME=~/.hermes` under `sandbox-exec` denying every file write outside your L1 scratch dir; network open for
  the model only. Guards: before the run, check (without printing any token) that every anthropic credential in the
  pool expires more than 60 minutes from now, else stop and report; turn MCP servers off for the run if Hermes has a
  documented switch, otherwise note which ones started; at most 14 turns; no retries if it fails. The coordinator
  checked: the live credential is a subscription OAuth token (no paid key), valid for about 6 hours now.
- Desktop REST mount: you now own `plugin/dashboard/`. Add the manifest and `plugin_api.py` shim and its test.

## New work
1. Private-import risk (ADR 0002 gate). A catalog rejection for private imports would lose the entry, so settle it:
   find the documented public way for a plugin to run a streamed, tool-using agent turn for the user (the gateway
   session protocol or any other public API). If it exists, build that driver behind your same driver interface and
   measure it in the same latency run as the embedded driver. Ship as default the driver that passes
   `hermes plugins validate` without the private-import warning, unless its measured full-turn latency is more than
   1.5x the embedded one; then report both numbers and stop for a coordinator decision.
2. Send L3 nothing; the pane direction is waiting for Can.

## LIMITS
Same as your brief, plus: the only run against `~/.hermes` is the guarded latency run above; it must write nothing
there (check with a before/after `shasum` of `~/.hermes/config.yaml`, `auth.json`, `state.db` and report the result).

## DONE
- Latency numbers (first text, full turn, p50/p90) per driver, with the command and the before/after hash check.
- `validate --install-deps` output for the chosen default driver.
- Test output for `tests/l1/`. Final report: commit SHA, which driver is default and why.
