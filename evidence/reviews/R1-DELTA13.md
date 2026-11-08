# R1 delta 13 — Delta hold trial, engine review

**Verdict: NO — not ready on the engine side.** Fix N16 and N17 below before the proposed Delta hold trial. Both are Medium findings with concrete local reproductions. No new Critical or High finding was established in this scoped review. Passing the engine review would still require the separately requested L6 round-10 review; this report does not approve a live run.

Reviewed source: main `1d58962212e2d897aa690d724e5c654ef97512be`, including `eb747b7`, `6c6b16d` and `0764706`. Main advanced to `c345e98d068fe694efc3f9d952150657f59b6e7a` during review; the only intervening change was one line in `STATUS.md`, with no plugin or test change. File:line references below are relative to the reviewed repository.

Scope: round-16/17 engine changes, task restrictions, human/ring transition, fixed fallback, inbound screening, live-leg bridge, and the L2 hold-trial plan. Intended configuration: ConversationRelay, owner mode, OTP off (`sms_source: none`), browser fill off, approved Delta destination `+18002211212`, configured Spectrum owner destination, owner ring timeout 20 seconds, and the approved checked-bag-fee fallback question. Keep `owner_live_bridge` off for this outbound ring-and-accept trial. The user supplied destination approval; this offline review did not independently verify current number ownership or Delta's actual menu.

## Findings

### N16 — Medium — forbidden menu actions still pass through speech and unrecognized payment wording

**Evidence:** `plugin/line/engine/machine.py:865–876` checks a spoken menu response against vocabulary only; it does not apply callback evidence or task restrictions to that response. `plugin/line/engine/fallback.py:37–41,76–78` expressly allows `yes` and `no`. The DTMF path does check restrictions (`machine.py:1114–1143`), but `plugin/line/engine/task_rules.py:35–44,67–82` recognizes only narrow option/category wording and allows other keys. Its payment category includes `pay`, `payment`, `billing`, and `card`, but not `charge` or authorization wording.

**Reproductions:** `fixtures/probe_hold.py` runs the real local Relay/menu handler with a fake provider and controlled model output, using the information-only task that automatically forbids callbacks, payments and changes.

1. Menu: `To get a call back when an agent is available, say yes. To stay on the line, say no.` Model reply: `yes`. The engine sent a Relay text frame saying `yes`. L2 had correctly supplied `callback offer requires task authorization; otherwise stay in queue`; the output guard nevertheless allowed acceptance.
2. Menu: `To authorize a charge, press 1. For an agent, press 2.` Model tool action: press `1`. The tool returned `ok: true` and Relay received `sendDigits: 1`.

A remote menu, misleading recognition, or prompt-influenced model can therefore turn an explicitly forbidden action into an audible acceptance or keypad selection. The demonstrated consequence is unauthorized acceptance signaling on the approved business leg. The fixture does not establish that Delta offers either exact menu or that money would actually be charged without account information. The narrow call scope supports Medium severity, but the result directly violates this trial's no-callback/no-payment conditions.

**Smallest effective fix:** validate both speech and DTMF against the current menu's permitted action at the final output boundary. For this restricted trial, permit positively identified safe navigation, stay/decline, and representative requests; ambiguous acceptance/payment/change actions must fail closed. A callback offer must never get an affirmative response just because `yes` is in the vocabulary. Expanding a payment synonym list alone does not repair the fail-open rule. Preserve the distinction between selecting a department and authorizing an actual booking change.

**Regression coverage:** `tests/l1/test_r16_hold_trial.py:125–148` genuinely exercises callback DTMF denial, stay-key allowance, uncertain callback-key denial, and the literal phrase `make a payment`. `tests/l1/test_r16c_fallback_question.py:158–186` checks single-key limits, callback DTMF and rejection of a spoken name/number. They do not cover affirmative speech accepting a forbidden offer or payment authorization expressed without the recognized words. Add both reproduced cases, uncertain menu actions, and speech/DTMF parity.

### N17 — Medium — an earlier menu turn can speak after the fixed-line human phase starts

**Evidence:** `plugin/line/engine/machine.py:733–747` transitions to a confirmed human, speaks the holding line and rings the owner without invalidating or interrupting the outstanding menu turn. New human speech is correctly routed to the fixed script at `machine.py:609–611`, but the earlier menu turn completes through `machine.py:865–876`. Its emission condition checks the generation and whether it pressed keys, not whether the call is still in that menu phase. The generation remains valid. `machine.py:781–798` invalidates an earlier turn when starting another turn; this fixed-line transition starts none. Owner acceptance later invalidates it (`machine.py:1354–1357`), leaving the preceding ringing window exposed.

**Reproduction:** the third case in `fixtures/probe_hold.py` delays the fake model's reply to a baggage/reservations menu. It then feeds the existing human-confirmation fixture through Relay and waits for `ringing_owner`. Releasing the delayed model produces an audible `yes` after the human transition and the engine's fixed holding line. No internal state was forcibly changed to create that transition.

A real representative who arrives while an IVR response is pending can hear an unintended affirmation; the remote party can also time an approval question to this window. This breaks the promised fixed-line conversation and can be interpreted as assent. The demonstrated extra speech remains subject to the limited vocabulary; this is not evidence of arbitrary speech or a new destination dial.

**Smallest effective fix:** invalidate the menu generation and interrupt/cancel its pending work before entering the human/fixed-script phase. At speech and tool dispatch, also require that the originating menu turn still belongs to the current permitted phase/prompt. Cancellation alone is insufficient if a worker completes late. Retain the existing single-use owner grant and owner acceptance gate.

**Regression coverage:** `tests/l1/test_r16c_fallback_question.py:54–130` verifies fixed responses and absence of newly started business turns, but begins the human phase without a pending menu reply. Add a delayed-menu fixture crossing human detection, owner ringing, fallback, and acceptance; assert no stale speech or tool effects in each case.

## Other requested checks

**Hold announcements and owner rings.** The former assumption path is fixed for owner mode: two unclear replies stay quiet rather than becoming a human (`machine.py:691–724`). An outbound ring independently requires `human_confirmed`, except for the owner's own take-over action (`machine.py:1220–1227`). L2 evidence now accompanies IVR prompts (`machine.py:651–673`). The selected round-16 tests exercise known artifacts, repeated unclear responses, clear confirmation, and a genuine detector HUMAN result. L2's 83 tests include the synthetic long-hold corpus.

This is still transcript classification, not authenticated acoustic liveness. A recording or recognized announcement identical to an accepted agent introduction, or a clear affirmative within the confirmation window, can produce `human_confirmed`. The L2 hold plan explicitly documents that limitation. Thus the evidence supports no assumption-based ring and the tested announcement behavior, not “no possible recording can ring.” A false ring remains confined to the approved owner destination and requires the owner to press 1 to bridge.

**Owner grant, acceptance and fallback.** The owner destination is configuration-derived and passes the destination/consent gate immediately before provider placement (`machine.py:1229–1253`). The private acceptance path requires the ringing state and `1` (`machine.py:1342–1363`). The task timeout is applied to provider ringing and a local deadline (`machine.py:1252–1269`). The local deadline begins after placement returns, so it does not count provider-request latency as part of the 20 seconds. Timeout/decline leads to the one fallback script (`machine.py:1411–1423`), with one question, at most one fixed clarification, bounded answer collection, thanks and hangup (`machine.py:1272–1336`; `fallback.py:24–29,85–98`). New human input is not sent for an ordinary model response. N17 is the outstanding exception for work already in flight. The fixed summary quotes masked received text; it does not ask a model to invent an outcome.

**Inbound screening press-1.** `machine.py:567–588` and `screening.py:15–32` permit one fixed `1` during the first 15 seconds of an inbound Relay call matching the screening wording. It is not carrier-authenticated and does not require a configured forwarding number. An ordinary caller can imitate that prompt and cause the same `1` on their existing leg; the code bypasses normal menu authorization for this specific action. Repeats are ignored, other digits cannot be selected, and the branch grants no PIN authentication, destination, owner bridge or new dial. This bounded behavior is not a blocker for the outbound Delta trial: outbound calls are expressly excluded, with a regression at `tests/l1/test_r16b_screening_notes.py:99`. If screening must be carrier-only, narrow admission to an expected forwarding flow rather than treating prompt text as proof of carrier identity.

**Live-leg bridge.** Default is off (`config.py:46`). Eligibility requires the flag, owner mode, a PIN-verified inbound parent whose stored counterpart is the configured owner, a live registered session, and no current child (`service.py:697–707`). Actual joining rechecks the parent/child and confirmed human, consumes the task's owner-bridge grant through the destination gate, and moves existing legs rather than creating an owner dial (`service.py:757–816`). Linked teardown and pre-bridge ring-back behavior are explicit (`service.py:719–743`). The 15 round-17 tests exercise default-off behavior, owner binding, expired approval, gate refusal, unrelated busy calls, missing human/bridge approval, cancellation and linked hangups. Keep this feature disabled in this trial; its opt-in does not replace the requested private ring/press-1 acceptance.

**One-note-offer filter.** This is a speech filter, not an authorization boundary. Its tests cover repeated offers and the caller explicitly requesting a note. The information-only human path should not invoke that model response path at all; the pending-turn gap is N17. Do not rely on the note filter for this trial's restrictions.

**Long-hold time and spend.** The engine has a monotonic duration watchdog with up to five seconds polling lag (`machine.py:1630–1637`), a Relay-drop watchdog (`machine.py:1624–1628`), and provider leg limits. The owner leg currently receives the full configured duration, not the remaining trial lifetime (`machine.py:1253`). These are not by themselves the plan's global 45-minute/spend envelope. L6 must still enforce the deadline from the first business create attempt, remaining lifetime for owner/conference work, cleanup exposure, ongoing spend reservation/checks, menu time/retry stop conditions, and no redial. The plan's 45 minutes means total lifetime including menu/bridge, not 45 minutes of hold plus extra time. No live price, balance, real-time 45-minute soak, tunnel behavior or carrier timing was verified. These are pending integration obligations, not a claim that the forthcoming L6 code has failed review.

## Commands, results and limits

Tests ran against the disposable source export, using the already installed local Python, synthetic principals/model responses/provider adapter, and loopback fixtures. All writes were inside this review's scratch directory. No checkout or git writes, external network, Twilio calls, real credentials or reads of `~/.hermes-line/` were used.

With `PYTHONDONTWRITEBYTECODE=1` and `PYTHONPATH=plugin`:

```text
python -m unittest discover -s tests/l1 -p 'test_r16*.py' -v
  32 tests, OK, 16.972 s                         r16.log
python -m unittest discover -s tests/l1 -p 'test_r17*.py' -v
  15 tests, OK, 4.867 s                          r17.log
python -m unittest discover -s tests/l2 -v
  83 tests, OK, 0.081 s                          l2.log
python ../fixtures/probe_hold.py
  exit 0: all three unsafe behaviors reproduced probe-final.log
```

The probe's assertions deliberately establish the unsafe behaviors; exit zero is not a security pass. Its initial extra race attempt did not reach human state because the chosen introduction remained IVR/unknown in that context (`probe-extra.log`); replacing it with the suite's normal live-person question/confirmation sequence reproduced the race. The first two probes succeeded in both versions.

Execution used `sandbox-exec` to deny external networking, allow loopback, make the export/toolchain read-only, deny the real line-state directory, and restrict writes to fixture scratch. Environment was explicitly allowlisted with scratch-local HOME/TMPDIR/HERMES_HOME and a nonfunctional fixture `hermes` command. Limits: 120 CPU seconds, 32 MiB maximum file size, 256 open files, 1024 processes, and 180-second suite / 60-second final-probe wall time. This used host loopback as expressly permitted by the coordinator, not a separate network namespace; it did not provide an aggregate memory/disk quota. These are execution limitations, not evidence of live isolation or a complete audit. Security-audit guidance was used within the coordinator's narrower scope and write limits; no separate audit document set or agent workflow was created.

The selected 130 tests genuinely cover their named positive/negative cases. They do not establish universal task-action enforcement, stale-turn safety, actual provider behavior or long-hold financial bounds. The two findings above must be fixed and regression-tested, followed by the separately scoped L6 hold-command review.
