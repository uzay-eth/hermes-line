# Review R1: security review of call gates, webhook auth, consent and OTP release

## PURPOSE
Hermes Line is a Hermes Agent plugin that places real phone calls for its user, answers callbacks and releases SMS verification codes. A hole here means calls to numbers the user never approved, an emergency dial, a forged webhook steering a live call, or a leaked code. This is an independent review before the first real calls; the authors were Claude models, so you are the other model family.

## TASK
Review main at commit 202349c, read only. Scope: `plugin/__init__.py`, `plugin/line/server/`, `plugin/line/engine/`, `plugin/line/store/`, `plugin/line/policy/`, `plugin/line/sms/`, `plugin/dashboard/`, and their tests in `tests/l1/`, `tests/l2/`, `tests/l4/`. Context: `docs/BRIEF.md`, `docs/CONTRACT.md`, `docs/adr/0002*`, `docs/adr/0003*`, `docs/lanes/L1.md` (section on webhook auth without the Auth Token), `docs/lanes/L2.md`, `docs/lanes/L4.md`.
Check, with file:line evidence:
1. Every path that can create a call or a call leg (outbound, owner ring, conference participant, retry, callback redial, transfer) passes the destination gate and single-use owner consent immediately before the provider call; no path accepts a number from the model, speech, DTMF or a webhook parameter.
2. Emergency, short-code, Bora ([redacted]) and non-US denies cannot be bypassed (encoding, unicode digits, prefixes, SIP URIs, settings, a hand-built settings object).
3. Webhook and stream ingress: signature mode and the no-Auth-Token fallback (path secret, per-call tokens, REST CallSid check). Find forgery, replay, token reuse, timing, and fail-open paths. Judge whether L1's list of what the fallback does not protect is complete.
4. Consent: issue/consume binding, reuse, expiry, cross-task use, process boundary; that phone speech or model output can never count as owner approval.
5. OTP: sealed delivery only; code never in a tool result, transcript, log, event, Desktop snapshot or persisted store; destination binding (origin, call counterpart); released set persisted.
6. Desktop REST: owner-only, idempotency and revision checks, no way for a remote party to drive controls.
Run the three test suites (commands are in the lane docs) and say whether the tests actually exercise the claims.

## LIMITS
- Read only: no code changes, no git writes, no commits. Write only your report at `/Users/mac/.hermes/cache/scratch/hermes-line-r1/REVIEW.md` (create the folder).
- No network, no Twilio, no phone, no keys. Do not read anything under `~/.hermes-line/`.
- Assess real risk, not style; rank findings Critical / High / Medium / Low, each with file:line, a concrete exploit or failure, and the smallest fix.
- Owner decisions: if you need a decision from Can (the owner), do not ask Can and do not write a decision block yourself. Put the question in your final report to the coordinator with options and a recommendation.

## DONE
- `REVIEW.md` with findings, the test commands and results you ran, and a verdict: ready for first real calls to the owner's own phones, yes or no, and what must be fixed first.
- Final report: verdict and the Critical/High findings in one line each.
