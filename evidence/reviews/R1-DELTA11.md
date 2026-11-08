# R1 delta 11 — N12–N15 fixes

Reviewed **b5db97c97993e1efe1b14e021a2fa6e7ea4b8189**, exported from local main, including 76b45e1, 3e9671b, 96e0f79 and d3fc6ef; L1 section 23.

**Verdict: NO** for the attended owner-PIN → call Fi → bridge-owner trial (OTP off, ConversationRelay). N12 remains High: arming still does not establish that the read-back was heard. N14 remains Medium: the reusable PIN can still enter transcript/model history in split speech. N13 and N15 are fixed for their reported failures. No Critical finding or separate new finding identifier is needed.

| Finding | Status | Evidence |
|---|---|---|
| N12 — approval before read-back | **Partly fixed, High residual** | `machine.py:197–198,584–585,793–840` |
| N13 — redial resets attempts | **Fixed** | `store/db.py:429–470`; `service.py:445–464,811–821,1275–1276` |
| N14 — PIN reaches Hermes/transcript | **Partly fixed, Medium residual** | `machine.py:186–190,445–457`; `owner_line.py:64–107` |
| N15 — pause timer cancels hang-up | **Fixed** | `machine.py:717–753` |

All paths in this report are relative to the exported repository snapshot in `disposable/`.

## N12: the timer is not a playback boundary

The old unsent-read-back probe now passes: `owner_new_task` creates a proposal, not a confirmable request; early speech invalidates it. Frame arrival timestamps, a generation, unrelated-turn invalidation and the 20-second response window improve binding (`machine.py:370–391,601–604,767–840`). The tests for these behaviors genuinely exercise them.

However, `_after_turn` sends the text and starts an arming sleep using `speech_wait(text) + speech_wait(line)` (`machine.py:833–840`). `speech_wait` is a goodbye heuristic capped at **eight seconds per string** (`machine.py:197–198`). It does not observe synthesis, network delivery, the remote playback queue or completion. The ordinary `_send`/`_writer` path is asynchronous (`machine.py:334–358`); enqueueing text does not mean the owner heard it.

The residual probe uses the production default 14 characters/second and an allowed task title. Its 217-character read-back has an uncapped estimate of **15.5 seconds**, but arms after **8 seconds**. This discrepancy exists even by the implementation's own rate model, before adding provider latency. I also held outgoing delivery in the fixture while allowing ingress to continue. After the timer armed, a relay interruption followed by “Yes” created a voice approval ticket although the fixed confirmation had never reached the fixture recipient. The fixture models delayed delivery; it is not a measurement of Twilio playback.

The interruption safeguard does not repair this: `_on_interrupt` invalidates only `proposal`, not an already armed `pending_confirm` (`machine.py:584–585`). Once the early timer fires, an owner speaking over the still-playing read-back can approve it. Arrival timestamps measure when ASR frames reach the plugin, not when the caller began speaking; delayed recognition is another reason the timer cannot establish audible completion.

**Impact:** an affirmative to earlier speech or injected audio can still authorize a model-selected task before the owner has heard its destination/request. Destination policy remains enforced; this is the same consent defect, with a narrower timing window.

**Smallest effective fix:** do not use a capped text estimate as an authentication/consent boundary. Use a reliable playback-completion signal if the chosen transport provides one; otherwise require a fresh challenge response delivered only at the end of the fixed task/destination read-back, or route this approval through the existing owner surface. Invalidate on any interruption while confirmation is pending. Removing the eight-second cap is useful but does not by itself prove delivery or audibility. Add delayed delivery/playback, long read-back, post-arming interruption and delayed-ASR tests.

The seven N12 tests at `tests/l1/test_r13_owner_line.py:452–544` cover unsent proposals, delayed model completion, pre-arming queued frames, early barge-in, expiry, unrelated replies and replacement. They wait for or manipulate internal arming; none proves playback finished. The documentation explicitly calls duration estimated, but that limitation leaves the security claim unresolved.

## N13: persistent budget and recovery

The delta-10 redial probe now finds no PIN prompt and no active PIN phase after three failed attempts; supplying the correct fixture PIN on the locked call does not verify it. The cooldown and daily budget are keyed by configured line, not spoofable caller ID. A hard lock persists until explicit unlock. Success resets only the streak, not the daily history.

`owner_auth_failure` updates rows inside `_tx` (`store/db.py:439–460`), which uses SQLite `BEGIN IMMEDIATE` (`db.py:147–157`). An independent fixture used two Store connections and simultaneous threads, recording ten failures without loss; the result was a hard lock visible through both connections. The lock remained hard under both forward and backward test timestamps. This tests concurrent store writes, not two live provider calls.

The normal service serializes inbound admission under `_line_lock`, refuses a second live call (`service.py:1227–1241`), and requires the exclusive line lease at startup (`service.py:199–226`). PIN comparison, success reset and failure recording have no await between them in `_pin_attempt` (`machine.py:723–738`). Thus two simultaneously admitted PIN calls are not a supported state; I found no remotely reachable lost-budget race in this architecture. The separate lock-check/compare/store operations should not be mistaken for one database-level authentication transaction if multiple active service writers are ever allowed.

**Clock behavior:** cooldown and rolling-day history use supplied wall time (`db.py:429–459`). A forward clock jump can expire a cooldown and age failures out before the real elapsed period; a backward jump extends restrictions. A hard lock itself is independent of time. A remote caller has no clock-setting capability in this review, so this is a host-clock dependency, not a confirmed remote bypass. The current tests do not simulate clock rollback/forward jumps before reaching a hard lock. Keep this limitation explicit; persisted nondecreasing time accounting would make the cooldown/day semantics more robust.

**Unlock and alerts:** `unlock_owner` requires `OWNER_SOURCES` (slash/Desktop), while call-agent and owner-voice sources are rejected (`service.py:802–821`). The slash command is wired at `hermes_glue.py:414–415`. Each new lockout creates an outbox notice without PIN digits (`service.py:450–464`). Tests verify actual fixture delivery, hard-lock recovery and rejection of tool/call-agent/owner-voice unlock attempts.

**“Locked line offers nothing”:** it offers no PIN authentication and does not compare DTMF guesses. It still provides ordinary inbound greeting/message handling and the pre-existing, configured single owner-ring grant (`service.py:1275–1292`). Its context may say the caller is most likely the owner based on caller ID, but `owner_verified` remains false and the new-task gate refuses it. Lockout is of owner mode, not a shutdown of all inbound service. I found no new owner privilege from this behavior.

## N14: original case fixed, scrub coverage incomplete

Keypad-only verification and retaining PIN entry through an initial greeting fix the original probe. Exact full PIN speech after “Hello” becomes `[PIN]` before the transcript/detector/Hermes; keyed entry still verifies. `_transcript` adds a second pass for every speaker (`machine.py:186–190,445–457`). The new regression at `tests/l1/test_r13_owner_line.py:68–91` checks full words, numeric variants, selected three-digit partials and repetition after verification. It passes.

**Residual reproduction:** send “four eight” and then “two one” as separate final speech frames for the synthetic PIN 4821. Both fragments remain in the persistent transcript and Hermes turns, exposing the entire reusable PIN across adjacent messages. Even a single frame `four eight; two one` is unchanged by `scrub_pin`: the semicolon splits runs because `_JOIN` permits only whitespace, comma, period, hyphen and slash (`owner_line.py:64–101`). Two-digit pieces match neither the full PIN nor the chosen three-digit prefix/suffix. No real credential was used.

The advertised “all but one digit” handling also only constructs the prefix and suffix of length PIN-minus-one (`owner_line.py:95`); it is not a general missing-digit matcher. Most importantly, each message is scrubbed independently before it is immediately forwarded. Later reconstruction cannot remove an already delivered secret fragment from Hermes history.

**Boundary audit:** the new PIN-specific scrub is called only by `_on_speech` and `_transcript`. `_event` sends directly to the store (`machine.py:183–184`); generic store masking is not configured with the PIN. `speak` gives the original text to driver notes (`machine.py:594–599`), `_on_interrupt` passes the original interruption text to the driver (`machine.py:584–591`), and task/summary storage retains the prior generic masking. These are not uniform `[PIN]` boundaries. I do not claim each independently discloses a key without a source of key-bearing text; the confirmed source is split owner speech reaching transcript and model history. Snapshots/evidence derived from those transcripts inherit the disclosure. No actual provider logs or exported live evidence were inspected.

**Smallest fix:** while PIN entry is active, do not forward or persist free-form speech that can be digit entry; provide a fixed keypad instruction and an explicit exit from authentication mode. For repeat/partial protection outside that phase, buffer potential credential fragments before forwarding and cover punctuation normalization, or adopt a more conservative digit-speech suppression policy. Apply secret-aware sanitization consistently to notes/events/summary/export boundaries rather than relying solely on transcript cleanup. Test split frames and punctuation with synthetic secrets. Do not claim keypad-only authentication alone guarantees secrecy of a mistakenly spoken PIN.

## N15: fixed

`_pin_attempt` now avoids cancelling its own timer, and the goodbye/hang-up runs in a separate task (`machine.py:720–722,748–753`). The adapted three-pause probe reaches terminal state. The two new tests at `tests/l1/test_r13_owner_line.py:299–328` cover both failed pause submissions and successful pause entry; they pass. This fixes the previously demonstrated self-cancellation path.

## Execution and evidence

All work was read-only against the checkout; the exact main hash was exported to scratch. Existing Python 3.11 was used under the delta-10 sandbox: external networking denied, loopback allowed, scratch-only writes, clean environment/scratch HOME and TMPDIR, no bytecode, and an explicit deny for the real `/Users/mac/.hermes-line`. No phone, provider, real-key, tunnel or gateway access occurred. Resource and wall-time limits were retained; host loopback was used without a separate namespace or hard memory limit.

Working directory: this report directory's `disposable/`. Interpreter: `/Users/mac/.hermes/hermes-agent/venv/bin/python`. Wrappers, fixtures and logs are retained locally.

| Command | Result |
|---|---|
| `python -m unittest discover -s tests/l1 -p test_r13_owner_line.py -v` | **26 passed**, 21.156 s; `r13.log` |
| `python ../fixtures/probe_owner.py` | Exit 0: adapted original cases pass; N12/N14 residuals reproduced; `probe.log` |
| `python ../fixtures/probe_store.py` | Exit 0: concurrent increments retained, hard lock survives clock shifts, punctuation scrub gap reproduced; `store.log` |

The original four probe scenarios were adapted to require the intended fixes; separate assertions establish the residual failures. Full historical L1/L2/L4 suites were not rerun. No live audibility or ASR timing was inferred from offline tests. Before the requested trial, close the N12 playback/confirmation boundary and the N14 disclosure paths. No owner permission question is needed to make those defects concrete.
