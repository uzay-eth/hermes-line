# Attended Hermes Line trial

Use the existing Hermes interpreter; no package installation is needed beyond
`HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_ANALYTICS=1 brew install cloudflared`.
This harness depends on this machine's existing L1 scratch interpreter and pinned
Hermes site-packages (the paths are in `run.py`). It is a trial tool, not a portable
plugin installer. Personal numbers are read only from
`~/.hermes-line/personal_numbers.json` (mode 0600, keys `owner` and `fi`);
the plugin line comes from `~/.hermes-line/twilio_line_number`. Missing or malformed
files stop the command. The allowlist is exactly those two personal numbers.
Use `python3 tools/coordinator/scan_personal_numbers.py` to check tracked files;
its output contains paths and counts only. It loads the checkout's production `CallService`, not a copy.

```sh
/Users/mac/.hermes/hermes-agent/venv/bin/python -B tools/trial/run.py preflight
/Users/mac/.hermes/hermes-agent/venv/bin/python -B tools/trial/run.py dry
```

Both commands start their own disposable service and quick tunnel, check public
HTTPS and WSS, then stop both. Preflight reads only Twilio's Balance resource.
Dry reuses preflight's balance evidence (maximum age 30 minutes), uses the fake
provider, and attaches to the owner's already-running gateway for one session
named `hermes-line trial dry <UTC time>` in `hermes-line-calls`. It asks the model to find and invoke only the
session MCP finish tool. Model inference uses the owner's existing route. No
phone call is made. The fake provider still uses the production Twilio request
encoder and actual service-generated callback/TwiML/relay URLs; reports replace
their path secret and relay token. All SIDs in dry/preflight stores are synthetic.
The printed form is a dictionary summary: repeated `StatusCallbackEvent` fields
show their final value (`completed`); the production encoder sends all four
events (`initiated`, `ringing`, `answered`, `completed`).
The probes create a synthetic session without dialing, so they work during
quiet hours. They do not demonstrate that a real dial is allowed at that time.

Only the coordinator runs the following, after the budget/call authorization has
reached them, while Can is ready to answer, outside 21:00–08:00 Pacific quiet hours:

```sh
/Users/mac/.hermes/hermes-agent/venv/bin/python -B tools/trial/run.py call \
  --to [owner phone (Spectrum)] \
  --task 'Run the attended Hermes Line trial with Can. Disclose that you are an AI assistant. Exchange two short greetings, then finish the call. Do not ring or bridge another phone.' \
  --mode agent --max-minutes 3 --i-am-the-coordinator
```

The command stays in the foreground until the call ends and exports evidence.
There are no retries. Default and maximum trial duration are three minutes;
`--max-minutes` accepts 1–3. The plugin's watchdog and provider `TimeLimit` enforce
the cap. Balance is read before startup and again at the final provider boundary.
A local lock prevents concurrent invocations across checkouts. The adapter
permits only one create **attempt**, including when Twilio's response is lost.
A second leg is refused even if the model requests one. `--mode owner` selects
the plugin's owner plan but its owner-ring leg will be blocked by this trial's
one-call cap; use `agent` for the first trial. This harness does not validate
bridging. An ambiguous create timeout needs coordinator investigation; never
blindly repeat the command. The provider cap limits an uncorrelated leg.

The ready gateway is required; the harness never starts one. The viewer uses
Hermes's explicit-endpoint attach-only client and a write sandbox. Its bootstrap
uses pinned dependencies and suppresses install-maintenance functions in the
viewer process only, because current bootstrap otherwise tries to lock the
owner's install. No Hermes source/profile file is edited. Restarting the gateway
mid-call loses the tools, as documented in L1 section 14. The inference session uses the restricted `hermes-line-calls` profile: ACP
toolsets are empty, profile plugins and MCP servers are disabled, and only the
deferred-tool bridge plus the three session MCP phone tools are available. Dry suppresses detector/summary questions
to keep exactly one saved session; real calls retain the production driver.

From a second terminal, request clean shutdown with:

```sh
/Users/mac/.hermes/hermes-agent/venv/bin/python -B tools/trial/run.py down
```

Ctrl-C and SIGTERM also stop the foreground runtime. `down` uses a Unix socket
inside the private scratch directory, not PID-based killing. `down` reports a
request; the foreground process reports when cleanup has completed. The plugin
hangs up tracked legs before the tunnel exits. SIGKILL cannot run cleanup; Twilio's
TimeLimit remains the backstop. Failed and interrupted real runs retain their
private store; rerun `evidence` after resolving any provider metadata failure.

```sh
/Users/mac/.hermes/hermes-agent/venv/bin/python -B tools/trial/run.py evidence call_REPLACE_WITH_REPORTED_ID
```

This exports `evidence/trial/<call_id>/trial.json`: Call SID, plugin duration and
status, masked transcript and event log, balance snapshots and provider duration,
status and price when returned. Final Twilio price may be null initially. Evidence
only reads local captured data; it does not poll Twilio, so pending price stays
pending until a separately authorized coordinator refresh. It can export any
recorded trial, not only the latest. Runtime evidence writing is for the
coordinator's future invocation; the L6 worker never invokes it or writes there.
Never commit the private scratch directory or copy raw gateway/provider logs.

Credentials remain at `~/.hermes-line/twilio_account_sid`, `twilio_api_key.json`
(`api_key_sid`, `api_key_secret`) and `twilio_line_number`. The required secret
names from `plugin.yaml` are bound through real `agent.secret_scope` for each
worker read; no dotenv is written and no Twilio key goes into the viewer's
environment. The service has its own `HERMES_HOME` and SQLite store under
`/Users/mac/.hermes/cache/scratch/hermes-line-trial/`, directories 0700, files 0600.
No Auth Token or xAI key is inherited by the secret getter. SMS/OTP browser fill
are off, ConversationRelay/disclosure/quiet hours are on, recording is off, and
the allowlist contains exactly Can's two approved phones. Bora is hard-refused.

A Cloudflare quick tunnel is suitable for this short attended trial: it needs no
account, exposes only this process's loopback ingress, and is removed at cleanup.
Its origin changes every run; the harness supplies outbound call URLs directly
and never configures any Twilio number's webhooks. It is unsuitable for persistent
callbacks, unattended use or a stable installation. A normal installation needs
a stable named tunnel/domain or a managed HTTPS/WSS deployment, supervised service
startup, restricted secret storage and the preferred Auth Token signature mode.
Provisioning that setup is outside L6. Cloudflare terminates TLS; fallback ingress
retains L1's documented bearer-secret limitations.

For tunnel checks only, a system NXDOMAIN triggers DNS queries to Cloudflare's
1.1.1.1 resolver for the exact assigned hostname, with a brief propagation wait.
The override lives in this process, matches only that hostname, and preserves TLS
SNI/certificate checks and Host. It does not change VPN/system DNS or `/etc/hosts`.
The MCP listener is a separate ephemeral loopback port and is never tunnelled.

## Inbound trial (round 3)

Coordinator only, with Can ready and the owner gateway already running:

```sh
/Users/mac/.hermes/hermes-agent/venv/bin/python -B tools/trial/run.py listen --minutes 5 --i-am-the-coordinator
```

`--minutes` is required and accepts 1–10. The timer starts when the number is
configured; at the deadline the harness begins hanging up the active call and
restoring settings. Provider requests can add time to cleanup. One allowlisted caller is admitted, whether calling directly or
forwarded through Fi. Unknown/hidden caller IDs are refused in this restricted
trial, even though the production plugin supports them. Retries of the same
admission retain L1's idempotence; later distinct calls are rejected.
`forwarding_number` is Fi, `inbound_ring_owner` is on, owner is Spectrum and
`reply_budget_s` is 2.5. Quiet hours still prevent owner ringing. The trial permits
one owner-ring attempt to Spectrum, with a provider TimeLimit and a fresh balance
check; it cannot dial another destination. There is no outbound business dial.
The original `call --caller-id fi` and `policy.blocked_destinations: [BORA]` remain.

Only the number read from the private line-number file (the line) can be
configured. A lookup filters IncomingPhoneNumbers by that exact E.164 number;
no unfiltered number list, other number SID or Bora resource is followed.
The service's authenticated inbound URL and a trial status callback URL replace
VoiceUrl/VoiceMethod and StatusCallback/StatusCallbackMethod; VoiceApplicationSid
is temporarily cleared so it cannot override VoiceUrl. Other voice settings and
all SMS settings are left alone. The status route dispatches the accepted CallSid
to L1's original status handler, retaining account/path-secret/REST checks.
The admission guard narrows L1's original handler after its ingress auth checks.
No plugin source is modified.

Before the first mutation, the five previous fields are fsynced in the private
run folder's `voice-before.json` and the root's `voice-restore.json` journal.
Normal exit, timeout, Ctrl-C, SIGTERM, `down`, and install failures all attempt
restoration before the service/tunnel closes. Restoration is read back and
verified before clearing the pending journal. `down` waits up to 30 seconds for
the active command's lock; if still busy it reports pending cleanup and must be
retried. When no active process holds the lock, `down` or the next valid harness
command restores a crash journal before doing anything else. Pending recovery
can therefore make a number-settings API write even on startup of `preflight`,
`dry` or `evidence`. Recovery itself starts no gateway or tunnel.

Network/provider outages can prevent restoration: the journal then stays pending
and new work fails closed. Retry `down` when the API is available. SIGKILL/power
loss cannot run cleanup immediately; restoration requires the next invocation.
Keep the private trial root until restoration succeeds. An inbound call has no
outbound create-time TimeLimit; a killed process cannot guarantee its immediate
hangup. Normal shutdown hangs up the inbound and any owner leg. These are limits
of deferred crash recovery, not claims of uninterrupted provider availability.

Evidence is exported as for `call`, with provider metadata for inbound and owner
legs in `provider_legs`, plus the masked transcript, events and balance snapshots.
If no call arrives, the run retains a private report and balance snapshots with
no fabricated Call SID. Voice snapshots are private and never included in public
evidence, since previous URLs may carry credentials.


## Restricted call profile (round 4)

The trial sets `call_profile: hermes-line-calls` and the sandboxed viewer attaches
only to that profile. The existing owner gateway can serve it as a multiplexed
profile: a read-only runtime handshake verified PID 2495, control home
`~/.hermes`, and the new profile's identity. No gateway was started or restarted.
If discovery fails later, the harness refuses rather than starting a gateway.

The profile was created by `hermes profile create hermes-line-calls --clone`,
without `--clone-channels` or `--clone-all`. Its own `config.yaml` has
`platform_toolsets: {acp: []}`, an empty plugin enable list plus explicit denies,
all plugin entries disabled, and all four cloned MCP servers disabled. Its
`SOUL.md` is a neutral two-line note. The clone contains private credentials,
configuration, skills, identity files and installed plugin files, which are not
committed; plugin files remain installed but disabled. No other profile's files
were manually edited. The clone command also created its named wrapper in
`~/.local/bin/`.

Before a production call the L1 rule-12 guard checks `/tools` and refuses extra
or unreadable tools. Dry exercises the same guard and reports the actual
`/tools` reply plus the session MCP endpoint's `tools/list` result. The successful
round-4 dry listed only the three bridge tools, exposed exactly the three phone
tools through its MCP endpoint, and invoked the session finish tool. The profile
still uses the owner's cloned model route; it does not grant coding/browser/web
or unrelated MCP tools to a caller.


## R9 scope check (round 5)

The viewer now renders `/tools` from the authoritative, complete
`session.info.enabled_toolsets`, before display-time Tool Search collapse.
It preserves every toolset; missing/empty/malformed/duplicate scope fails closed.
L1's unchanged r9 guard binds that list to this call's unique `hl_<8 hex>`
identity and rejects extra scope. The normal formatter's bridge-only display is
never itself accepted. The dry report includes both original and scope replies,
with no system prompt or full session.info object. This is a viewer-local
projection (`scope.py`), not a profile, gateway-owner or plugin edit.

This supersedes the round-4 bridge-only admission evidence. A real gateway may
show bridge tools after deferring MCP schemas, while a scratch inventory with no
direct definitions shows `Enabled toolsets:` instead. Both can have the same
frozen scope. The cause of differing inventory population is not established;
the authoritative projection avoids relying on that display distinction.
The projection relies on the current Hermes session.info contract. Metadata
changes must fail closed and require revalidation, not a fallback to bridge names.


Round-5 live result: the full scope additionally contains `jev` and `x_search`,
so dry correctly refuses. No profile edit was made. Before retry, the coordinator
must merge these names into `agent.disabled_toolsets` in the call profile only:

```yaml
agent:
  disabled_toolsets: [jev, x_search]
```

Keep other existing agent settings and any existing disabled-toolset entries.
An empty ACP list alone does not suppress Hermes's auto-injected runtime plugin
and credential-backed toolsets. Disabling Tool Search would change visibility
without removing these grants. Retry with a fresh ACP session after the approved
profile edit; previously frozen sessions retain their old scope. The resolver
suppression was tested offline, not applied or validated live by this round.

## Grok transport and $2 trial allocation (round 6)

`call`, `dry` and `listen` accept `--transport relay|grok`, default `relay`.
Grok pins `voice.transport: media_streams` and the L5 provider/transcriber path,
with reasoning effort `none`. Only Grok invocations read the staged
`~/.hermes-line/xai_api_key` (one nonempty line, 0600). It is read once into the
same private in-memory secret-scope mapping as the Twilio values. No copy,
dotenv, child environment, report or ledger contains it.

```sh
/Users/mac/.hermes/hermes-agent/venv/bin/python -B tools/trial/run.py preflight
/Users/mac/.hermes/hermes-agent/venv/bin/python -B tools/trial/run.py dry --caller-id fi --transport grok
```

The Grok dry uses production Media Streams TwiML and a fake Twilio record, checks
HTTPS/WSS without sending a stream-start/audio frame, and checks the restricted
ACP finish tool. It then attempts exactly one real xAI connection with L5's
`grok-voice-latest` adapter and session.update configuration. No audio, text input,
response.create or STT stream is allowed. Setup is capped at 4.8 seconds plus
bounded teardown; a socket timer also aborts the transport. Reports retain only
session-created field names, never their values, plus connect/config timing.
It is a config/authentication check, not a voice-quality or first-audio test.

`xai-spend.json` under the private trial root is a persistent, fsynced ledger.
Before each Grok run it reserves maximum connected seconds at ADR 0004 prices:
$0.08/min realtime + $0.20/hour streaming STT. Dry reserves only five seconds of
realtime, because it prohibits STT. New reservations exceeding $1.80 are refused.
Pending reservations continue to count after a crash; never delete the ledger to
reset the budget. All writes are serialized by the harness's existing global
run lock; do not use provider factories outside that lifecycle.

Each L5 realtime/STT socket is wrapped to measure elapsed time, including setup
conservatively. It closes at the run's time cutoff and limits concurrent sockets
to one of each kind. Text item creation is debited at $0.004 before send,
including tool-result items conservatively, and refused if it would consume the
remaining budget beyond the time reservation. At cleanup the reservation is
replaced with measured seconds times rates plus text debits, rounded upward.
Incomplete connections are accounted conservatively for their elapsed setup.
The ledger is an estimate, not xAI's invoice; billing granularity/basis, taxes,
external key usage and provider-side delayed processing are not measured here.
The $0.20 gap to the owner's $2 cap is kept as reserve.

Preflight prints the local ledger. xAI's documented account usage/balance API
requires a separate management key, not the staged inference key:
[Management API guide](https://docs.x.ai/developers/management-api-guide),
[Billing reference](https://docs.x.ai/developers/rest-api-reference/management/billing).
No management credentials were requested and no authenticated usage request was
made with the inference key. Console/provider billing reconciliation remains a
coordinator task.

Future coordinator commands (not run by this worker):

```sh
/Users/mac/.hermes/hermes-agent/venv/bin/python -B tools/trial/run.py call --to [owner phone (Spectrum)] --task 'Run the attended Grok voice test with Can. Disclose that you are an AI assistant. Exchange two short greetings and finish. Do not ring another phone.' --mode agent --caller-id fi --max-minutes 3 --transport grok --i-am-the-coordinator
/Users/mac/.hermes/hermes-agent/venv/bin/python -B tools/trial/run.py listen --minutes 5 --transport grok --i-am-the-coordinator
```

All existing destination, consent, quiet-hours, one-leg, number-restoration and
balance guards remain. Evidence adds `grok_evidence`: measured realtime/STT
seconds, ledger before/after, connect timings, L5 voice_open events for human
conversation, first-audio events, barge-in events and masked business transcripts
(STT plus realtime input transcription). A dry run has no first-audio/human-answer
measurement; those fields are null/absent rather than zero-latency claims.
No plugin source was changed. Live audio, STT and barge-in still need the
coordinator's later call; only one audio-free realtime connect was exercised.


## N11 close accounting (round 7)

A socket now distinguishes stop intent (`closed`), close in flight (`closing`)
and completed underlying close. At close start it books elapsed seconds
synchronously in an accounting `finally`, before any await. Cancellation of the
outer or nested close still runs a second accounting `finally`. Concurrent
closers join the same owner operation; `close_all()` waits for it rather than
returning merely because stop intent was set.

`finish()` refuses while setup/close remains in flight or a socket is open. If
cleanup was cancelled/timed out, it uses wall time since connect as a conservative
floor. After the service loop is torn down, the harness synchronously aborts and
accounts abandoned sockets, including a close task cancelled before its first
turn; it never tries to drive a dead loop from a new loop. A later real close may
raise a settled charge but cannot lower it. Per-row locked journal merges protect
reservations written by later invocations from stale callback data.

The next valid startup (including preflight) holds the global trial lock before
reconciling interrupted pending runs. It charges at least the run's recorded
worst case plus pre-debited text, or a higher already recorded amount, and marks
it `reconciled_crash`. It never frees that reservation as unused credit. This is
local worst-case reconciliation, not provider billing reconciliation. Corrupt
journals fail closed.
