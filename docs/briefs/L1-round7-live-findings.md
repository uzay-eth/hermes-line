# L1 round 7 (coordinator): first live call findings, then callbacks

`git merge main`. Evidence: `evidence/trial/call_68921278eab742c6/trial.json` (masked) and the service stderr at
`~/.hermes-line/trial-logs/call_68921278eab742c6-stderr.txt` (contains no secrets by check, but do not copy it into
the repo). Owner call 1: Fi caller ID to the owner's Spectrum, ConversationRelay, ACP driver, agent mode.
Timeline: disclosure 16:42:59.7; owner "Hi. How are you?" 16:43:08.3; Hermes live-person question 16:43:09.9 (1.56 s);
owner "Yeah. It's me, Ken. But information you have about me?" 16:43:19.6; then NO reply for ~39 s; detect
`unknown/low` twice; owner hung up 16:44:18 (`relay_lost`, reconnect attempt 1, `business_hangup`). Stderr shows a
request stuck in `machine.py:1030` `driver.ask(..., ask_timeout_s)` and "timeout graceful shutdown exceeded" at stop.
Twilio logged notification 15003 on the call.

## 1. Silence (do first, own commit)
Write 3-4 theories before fixing and test them in parallel, for example: ACP `ask` cannot run while the session's
turn is open (serialised on one ACP session, waits for timeout); the live-person path waits on `ask` instead of
speaking; ask timeout 20 s too long for a live line; the reconnect after hangup was attempted on a completed call.
Fix what the evidence supports. Rule: a live person must never hear more than 3 s of silence; speak a filler and
keep the turn going while any classification runs. Test: replay this exact transcript through the real ACP driver
(fixture model) and assert reply latency for every human turn. Report the theories ruled out and how.
Also: an owner calling their own line in agent mode is a test setup, so do not ask "am I speaking with a live
person" when the answer already says so; pass the owner's and business name as ConversationRelay `hints` so "Can"
is not heard as "Ken"; explain notification 15003 and fix it if it is ours (status callback during shutdown).

## 2. Callbacks over a forwarded Fi line
Design (coordinator decision): the business sees the Fi line (verified caller ID) and calls it back; the Fi line
forwards all calls to the plugin's Twilio line; Hermes answers. Make inbound work for forwarded calls: read
`ForwardedFrom`/`CallerName` where Twilio sends them, match callbacks to open tasks by caller number and time window,
and for unknown callers greet with disclosure, take a message or ring the owner (owner approves by pressing 1).
The H2 inbound admission rules stay. Tests for: forwarded callback matched to a task, unknown caller, owner ring
declined, caller hangs up early. Document the user-side setting in `docs/lanes/L1.md` (one paragraph; the Fi steps
themselves are written by another lane).

Same LIMITS as your brief. No live call. DONE: theories + evidence, test output, commit SHAs.
