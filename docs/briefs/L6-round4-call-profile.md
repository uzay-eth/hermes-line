# L6 round 4 (coordinator): restricted call profile for the trial

`git merge main` (58a17b1: L1 round 8 refuses every call unless the call session's tools are only the call tools;
read `docs/lanes/L1.md` round 8 and `plugin/README.md` Setup step 4).
1. Create the Hermes profile `hermes-line-calls` on this Mac: `hermes profile create hermes-line-calls --clone`
   (no `--clone-channels`, no `--clone-all`). Then in that profile only: add `platform_toolsets: {acp: []}` to its
   `config.yaml`, replace its `SOUL.md` with a two-line neutral note (the call briefing comes from the plugin), and
   disable all plugins and MCP servers in it. Do not touch any other profile's files.
2. Find out from the Hermes source and one live check how `hermes --profile hermes-line-calls acp` reaches a gateway:
   does the owner's running gateway (PID 2495) serve it (multiplexed), or does it need its own gateway process? If it
   needs a new gateway process, or a restart of the running gateway, STOP after step 1 and report the options; do
   not start or restart any gateway.
3. If it works without a new process: set `call_profile: hermes-line-calls` in the trial config (`tools/trial/run.py`),
   run `preflight` and `dry` (no call), and show that the call session's tool list is only the bridge plus the session
   MCP, and that the dry finish works. A saved session titled `hermes-line trial dry <time>` in that profile is fine.
LIMITS as your brief (network: tunnel and balance read only; no `call`, no `listen`, no Twilio writes).
DONE: profile diff (files created/changed, no secret values), `dry` output masked, tests, SHA.
