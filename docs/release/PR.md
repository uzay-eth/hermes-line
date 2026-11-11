# Catalog PR text for NousResearch/hermes-agent

The pull request that adds this plugin to the Hermes plugin catalog, opened from `uzay-eth` (MIT-018). A file cannot
contain the sha of its own commit, so this copy points the screenshots at `main` and does not repeat the validation
output. The opened PR pins the catalog entry and every screenshot URL to the full 40-character release commit and
carries the `hermes plugins validate --install-deps` output from a fresh checkout at that commit.

Format followed (from https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/catalog-submission, read
2026-11-05): "Add one file, `plugin-catalog/<name>.yaml`" and "In the PR description, say what the plugin does,
which Hermes surfaces it uses, and everything rule 13 asks you to disclose. Add screenshots for anything with a UI."

---

**Title:** plugin-catalog: add hermes-line (phone calls with hold, bridge-in and approved SMS codes)

## What it does

Hermes Line gives Hermes a phone line on the user's own Twilio account. The user asks Hermes to call a business
and approves the call with `/line approve`. Hermes dials, says it is an AI assistant, presses menu keys, waits on
hold without talking, answers a business's automated assistant only with fixed routing phrases ("Representative.",
"Agent.", "Speak to an agent."), and when a person answers it either rings the user's phone and bridges them in, or finishes
the task itself from the title and goal the user approved, in a separate call profile without the user's memory,
persona or tools (the plugin README lists the stated limits). A summary goes back to the chat that asked for the
call. A Desktop pane shows the live call (state, time on hold, masked transcript) with Take over, Say this and Hang
up. On a restricted task the owner's phone rings only after a clear yes to "Am I speaking with a live person?" (or after
two human-like but unclear replies, so the owner judges), and
every fixed line waits for 1.2 seconds of quiet so Hermes does not talk over the business. With an Android phone on
USB or wireless debugging, an SMS verification code can be sent into the call as key tones or speech, after
the user approves which code goes to which call; the model never sees the code. Optional owner line mode lets
the user call their own line, key a PIN and ask Hermes to call a business; it is off unless a PIN is set and is experimental
(see "Tested live").

Adds `plugin-catalog/hermes-line.yaml` (source copy in the plugin repo: `docs/release/catalog-entry.yaml`).

## Hermes surfaces used

- `ctx.register_tool` (11 tools, all declared), `ctx.register_hook` (`transform_tool_result`,
  `transform_llm_output`, used to replace a released code with `[CODE]` in later tool results and replies),
  `ctx.register_command` (`/line`), `ctx.register_skill` (bundled `hermes-line` skill), `ctx.get_config`,
  `ctx.on_unload`, `ctx.ask_choice` (code approval, with a `/line confirm-code` fallback).
- `ctx.post_message` for the approval request and the call summary (into the originating chat's stored session,
  bound with `gateway.session_context.scoped_session_key` when the service thread has none); `ctx.inject_message`
  for one engine-built sentence (task id, title, status) to the requesting session, never the summary text. That
  injection is best effort: Hermes refuses it unless `plugins.entries.hermes-line.allow_gateway_injection: true` is
  set, which the setup does not ask for; the posted summary does not depend on it.
- Secrets: `agent.secret_scope.get_secret` for the names in `requires_env` / `optional_env`.
- Desktop: one `panes` contribution in `desktop/plugin.js` (imports only `@hermes/plugin-sdk` and `react`),
  `ctx.rest`, `ctx.onEvent`; backend REST in `dashboard/plugin_api.py`; events through
  `hermes_cli.plugin_events.broadcast_plugin_event`.
- Each call's conversation is a Hermes session through the documented ACP adapter (`hermes acp`) on the user's
  own Hermes, in a dedicated call profile. The viewer runs as `hermes --profile <call_profile> acp`. The in-call tools (press keys, finish, bring the
  owner in, and a new-call request that works only after the owner's PIN) reach that session as a session-scoped MCP server on loopback, passed in ACP `session/new` `mcpServers`.
- Missing hook we would ask for: ACP `session/new` cannot carry a toolset selection to the gateway's
  `session.create` `toolsets`, so the plugin bounds the call session's tools with a dedicated profile instead.
- Private imports, each guarded (a missing one refuses the call or skips the post, never crashes):
  `hermes_cli.profiles.get_profile_dir` and `hermes_cli.default_soul` (`DEFAULT_SOUL_MD`,
  `is_legacy_template_soul`) for the call-profile check, `hermes_yaml.safe_load` to read the call profile's
  `config.yaml` as Hermes reads it (PyYAML is neither a Hermes nor a plugin dependency), and
  `gateway.session_context.scoped_session_key` to post the summary into the stored session that asked for the call
  from the service's own thread. Seams we would ask for: a profile-directory lookup, the stock-SOUL check, a
  documented YAML reader, `post_message` to a stored session of the plugin's own
  profile, and `hermes acp` launch flags that hold after its own dotenv load (`acp_adapter/entry.py` `_load_env`
  reloads the profile's `.env` with override after `--ignore-rules` is set, so a `HERMES_IGNORE_RULES=0` in the
  call profile's `.env` undoes it; the plugin does not inspect that file, rule 11).

## Disclosures (rule 13)

- **Network, third parties:** Twilio (`api.twilio.com`; all call audio passes through Twilio; on the
  ConversationRelay path Twilio does speech-to-text and text-to-speech). xAI (`api.x.ai`), only if the user sets
  `XAI_API_KEY`: hold and menu audio to xAI speech-to-text, conversation audio to Grok Voice. The user's tunnel
  provider carries Twilio's webhooks and audio to the plugin. Masked call text goes to the user's Hermes model
  provider as a normal Hermes session.
- **Reads outside its own data:** the SMS inbox of a USB-attached Android phone via `adb shell content query`
  (read-only), when `sms_source: adb` (default; the phone is optional). Before each call, the call profile's
  `config.yaml` and `SOUL.md`, the lists of its `plugins` and `hooks` folders (and the size of its memory files),
  not its `.env`; whether the temp directory or a folder above it holds a `.git` or a project file; then the call
  session's tool list through `hermes acp`.
- **Processes it starts:** `adb`, with only `PATH`, `HOME`, `LANG` and `ANDROID_*`/`ADB_*` from the environment; one
  `hermes acp` process per call, which starts the call profile's Hermes gateway (idle-exit) if it is not running.
- **Background service:** with `enabled: true` (default `false`), a call service in the Hermes process listening on
  `127.0.0.1:8788` (exposed by the user's tunnel) and on a loopback-only port for the in-call tools (per-call
  256-bit bearer token).
- **Stored data:** `plugin-data/hermes-line/line.db` (tasks, calls, masked transcripts, events, SHA-256 hashes of
  released code messages, owner-PIN failure counts and lockouts) and a 0600 webhook path secret file. No credentials
  are stored by the plugin; the optional owner PIN is a Hermes secret (`HERMES_LINE_OWNER_PIN`, `optional_env`) and is
  never passed to the model, transcripts, events or logs.
- **The call session holds only the call tools (rule 12).** Calls run in a dedicated Hermes profile
  (`call_profile`) with no ACP toolsets. Before every dial the plugin reads the session's tool list and refuses the
  call unless it holds only the deferred-tool bridge and the call tools. The `hermes acp` child never inherits `HERMES_YOLO_MODE`, `HERMES_IGNORE_RULES`, `HERMES_ACCEPT_HOOKS`,
  `HERMES_SAFE_MODE` or `HERMES_IGNORE_USER_CONFIG`, and the driver declines every permission request. Tested with
  a real `hermes acp` and gateway (fixture model) and `HERMES_YOLO_MODE=1` in the service's environment: an
  unrestricted profile was refused before dialling, the restricted one finished its call.
- **The call session gets none of the user's memory, user profile or persona.** The setup creates the call profile
  without `--clone`, with `memory.memory_enabled: false`, `memory.user_profile_enabled: false`,
  `plugins.enabled: []`, the provider key in that profile, and Hermes's stock `SOUL.md`. Every call session starts
  with Hermes's own `--ignore-rules` (`HERMES_IGNORE_RULES=1` for the `hermes acp` child): no `SOUL.md`, context
  files, memory or preloaded skills. At approval, at inbound admission and before the session starts, the plugin
  refuses a profile with memory or the user profile on, a memory provider, `display.personality`,
  `agent.system_prompt`, `personalities`, `agent.personalities`, `prefill_messages_file`,
  `agent.prefill_messages_file`, `agent.environment_hint`, `agent.coding_instructions`, `platform_hints`, `hooks`,
  `mcp_servers`, a hook directory in `hooks/`, enabled plugins, a non-empty `MEMORY.md` or `USER.md` (size only,
  never read) or a `SOUL.md` other than empty or Hermes's stock or earlier auto-seeded text. The plugin does not
  inspect the call profile's `.env` (rule 11): the setup creates the profile fresh, so that file holds only the
  provider key the setup writes, and Hermes itself loads it into the call agent. An old `--clone` call profile must
  be recreated; the `/line approve` reply says so. This is a fixed list, not a check of everything Hermes loads. A refused profile dials nobody (the approval reply names each problem and the fix) and admits no
  inbound caller on either voice path; at service start the plugin posts it once into the chat of the latest task
  (of the last 50) that came from a chat, or answers the next `/line` command or `hermes_line_request_call` with
  it. The session runs in a fresh
  owner-only directory per call, removed after it (also after a failed start), under the first of `TMPDIR`, `/tmp`
  and `/var/tmp` with no `.git` or project file in it or above it; with none, no session starts. The `hermes acp`
  child gets an exact environment allow-list by name (`PATH`, `HOME`, `USER`, `LOGNAME`, `LANG`, `LC_ALL`,
  `LC_CTYPE`, `LC_MESSAGES`, `TMPDIR`, `HERMES_HOME`, `XDG_*_HOME`, `XDG_RUNTIME_DIR`, `SYSTEMROOT`, CA-bundle and
  proxy variables), so no prompt variable (`HERMES_EPHEMERAL_SYSTEM_PROMPT` and the like) and no other variable of
  the user's process reaches it when it starts the call gateway; the proxy variables pass as set. Stated limits in
  the plugin README: Hermes's runtime block (host OS, home and working directory paths, profile name, time zone),
  callbacks matched by spoofable caller ID (that task's title and goal), a call gateway that is already running
  keeps its own environment (prompt variables included), a managed configuration layer is not read, the call
  profile's `.env` is not inspected (Hermes loads it into the call agent; anything besides the provider key there,
  such as `HERMES_IGNORE_RULES=0`, reaches the call session), skills and unlisted keys are not read, the owner's own agent can use `hermes_line_say` without a per-sentence
  approval, and a relay chat with a limit under 1,500 characters can cut an approval part.
- **Goal at approval.** The approval message shows the whole goal the call session will get, in numbered parts of
  at most 1,500 characters when it is long (Hermes clamps a post to the chat's limit and does not split it; Discord
  allows 2,000, SMS 1,600), then the approval command. If a part cannot be posted, the task is withdrawn. The
  Desktop REST snapshot carries the goal as `task_goal`. The other party may hear any of it.
- **Call summaries.** The summary quotes the other party, so it is only posted to the chat that asked for the
  call (that stored session, also after a restart or a failed edit), marked as untrusted text from the call. The
  plugin also tries to tell the requesting Hermes session only that the call ended and its task status; Hermes
  refuses that injection unless `allow_gateway_injection` is on for the plugin. A task the user asked for on the
  phone has no chat; its summary is in the Desktop pane and the call status.
- **Phone safety defaults:** AI disclosure line on every outbound call (on by default); the plugin always sends
  `Record=false` to Twilio and has no recording feature; dialing only to an allowlist the user writes; every call
  needs the user's approval, which the model, tools and phone audio cannot give, also when the user turns the
  allowlist off (any sender the user's Hermes gateway admits can run `/line approve`; in a group chat that is every
  admitted member); an exact-number block list; one call per line; quiet hours 21:00 to 08:00 in the user's time
  zone; emergency numbers, short codes, N11, premium (900, 976) and non-US numbers always refused (US geographic
  and toll-free numbers can be called); SMS codes released only with per-code consent bound to sender, time window
  and destination call, and delivered only into that call (no browser fill in this build).
- **Owner line mode (optional, off unless a PIN is set):** a call from the owner's number is only offered a keypad
  PIN; caller ID alone approves nothing. Wrong PINs count per line across calls and restarts (3 lock it for 15
  minutes, 10 in 24 hours lock it until `/line unlock-owner`). A call requested by phone is approved only when the
  caller keys digits of the number the plugin read back: the last four for the owner's own or an already approved
  number, otherwise the last seven; speech never approves. PIN tones pass through Twilio.
- **Speaking to automated assistants (restricted tasks):** until a person is detected, only fixed phrases chosen by
  rule are spoken, at most one every 8 seconds and 12 per call; requests for a name, number, code, date, card or
  identity get "Agent."; callback, payment and change offers get nothing. Keypad menus are answered only when every
  sentence matches an allowed greeting, hold line or option.
- **Verification codes come only from a carrier phone.** Twilio numbers filter one-time-code texts (error 30038).
- **No telemetry.**

## Validation

`hermes plugins validate --install-deps` (cold, throwaway `HERMES_HOME`): "Validation passed. Exit status: 0"; every
check from manifest to desktop surface is ✓, with one warning (⚠ private core imports, catalog rule 9: the five
imports listed under "Hermes surfaces used") and the in-process isolation note for `ctx.post_message`. The opened PR
carries the output of this check run on a fresh checkout at the pinned commit.

Last cold run: see `tests/l1/evidence/validate.txt`. Offline tests on the release tree (2026-11-10): L1 605 (1 is skipped
where PyYAML is installed), L2 145, L4 360 (with `HERMES_AGENT_DIR` pointing at a Hermes checkout; without one 7
skip), L5 88 and L6 122 pass; L3 75 of 76 (the remaining test needs a local Hermes Desktop build). 15 of them (`tests/l1/test_s7_call_profile.py`,
`tests/l4/test_s7_adb_env.py`) cover the catalog review of `d92bc1fb`; 23 more (`tests/l1/test_s7_fix_round.py`,
`tests/l4/test_s7_hermes_contracts.py`, the latter on Hermes's own prompt builder, dotenv loader and message
posting) cover the reviews of that fix, 3 of them that no plugin code opens, loads or inspects the call profile's
`.env`. 3 more (`tests/l1/test_s19_no_pyyaml.py`) cover the review of `115faa3d`: a fresh neutral call profile passes
the check where PyYAML is not installed, and every third-party import in the plugin is declared in `plugin.yaml` or
ships with Hermes.

## Tested live

As of 2026-11-08 (evidence in `evidence/trial/`, `docs/release/REPORT.md` and the plugin README status table):
- Five calls with the author's own phones: three outbound (80 s with a 39 s silence bug; 43 s after the fix; 64 s
  on the Grok path with one question to Hermes) and two inbound through Google Fi "Always forward" (92 s, 121 s;
  Twilio got the original caller in `From`, no `ForwardedFrom`; Fi's "press 1 to take this call" prompt answered).
- Four calls to Delta Air Lines reservations (toll-free, approved by the owner); no person reached on any run.
  Run 1 (68 s): Hermes stayed silent under the menu gate at Delta's automated assistant. Run 2 (57 s): a false
  "person" verdict rang the owner (no pickup) and the fallback question went to the bot. Run 3 (retry, 14 min 3 s):
  the assistant transferred the call to the real hold queue (23 minutes announced); Hermes spoke into the hold queue
  seven times and the call ended at a service restart; fixed offline before run 4. Run 4 (44 min 43 s): one "Agent."
  to the assistant, then Delta's real hold queue in silence (0 phrases spoken into hold, no false "person" verdict),
  ended at the trial's maximum call length.
- Three calls to Hilton Reservations (toll-free, approved by the owner) on 2026-11-08: real keypad menus (the menu
  gate pressed nothing) and default transfers; real agents answered three times in two of the calls, and the
  live-person check missed every time, so the owner was never rung and no bridge happened. Fixed offline (round
  43c), not yet re-proven live.
- Twilio caller-ID verification of the Fi number; an SMS code read on a Pixel 10 Pro XL with the code masked.
- The Desktop pane inside a real Hermes Desktop (sandbox install, sample data).

Not yet shown live: detecting a real agent, ringing the owner for a real person, bridging, pressing a key on a real
menu, a business calling back, codes into a live call, owner line mode with the PIN, and barge-in on the Grok path. Owner line mode passed a scoped security review (R1b delta
35) for one attended trial calling the owner's own other line; two Medium limits remain for wider use. The plugin README has a status table with the evidence
for each item.

## Screenshots

Offline harness renders with sample data (the same four as the catalog entry; pinned to the release commit in the
opened PR):

![On hold](https://raw.githubusercontent.com/uzay-eth/hermes-line/main/tools/pane-preview/screenshots/round4/on_hold.png)
![A person picked up](https://raw.githubusercontent.com/uzay-eth/hermes-line/main/tools/pane-preview/screenshots/round4/human_detected.png)
![You are connected](https://raw.githubusercontent.com/uzay-eth/hermes-line/main/tools/pane-preview/screenshots/round4/owner_connected.png)
![Ended, with summary](https://raw.githubusercontent.com/uzay-eth/hermes-line/main/tools/pane-preview/screenshots/round4/ended.png)

## Checklist

- [ ] One file added: `plugin-catalog/hermes-line.yaml`
- [ ] `sha` is the full 40-character release commit; `version: "0.1.0"` matches `plugin.yaml`
- [ ] `requires_hermes: ">=0.22.5"` in the entry and in `plugin.yaml`
- [ ] `capabilities` match registrations (11 tools, 2 hooks, 3 env vars)
- [ ] Validation output at the pinned commit pasted above
- [ ] Submitted from the owner's account (rule 5)
