# Brief L2: Safety gates and hold/human detection

## PURPOSE
Hermes Line is a Hermes Agent plugin for the Nous plugin hackathon (deadline 2026-11-11 14:00 PT): Hermes phones businesses for the user and waits on hold until a human answers. It must never dial an emergency number, never call without the owner's approval, and must not ring the owner for a recorded "hello". This lane builds those guarantees and the detector, measured on a hold simulation.

## TASK
Read `docs/BRIEF.md` (verify sha256 346bbac78d9e7054ca55985ed0181b13449d2b7fa359cb593d870ad9d3eb167d, else stop), `AGENTS.md`, `docs/CONTRACT.md`, ADR 0003 and `docs/spikes/S2-telephony.md` (sections 2 and 6 and the offline hold-simulation plan in `docs/spikes/S2-real-call-test-plan.md`).
1. `line.policy`: destination gate, consent store, settings with safe defaults, quiet hours, exactly as the contract names them.
2. `line.detect`: `HoldTracker` and `classify` as named in the contract. Deterministic rules first (repeated prompts, queue phrases, IVR menus, voicemail beeps/phrases); the injected model is asked only when rules are unsure; uncertainty stays `unknown`.
3. `fixtures/hold-sim/`: transcript streams that imitate real call types (long hold with repeated messages, IVR trees, voicemail, a human who answers mid-message, a human who says only "hello?", a recorded "hello, thanks for holding"). Label each one as simulation.
4. An accuracy report: confusion matrix and false/missed-human counts on the fixtures, with the fake model and with no model.

## LIMITS
- No phone calls, no Twilio, no purchases, no API keys, no network.
- Write only `plugin/line/policy/`, `plugin/line/detect/`, `fixtures/hold-sim/`, `tests/l2/` and `docs/lanes/L2.md`. Commit locally on your branch; no push.
- Standard library only.
- The emergency, short-code, Bora ([redacted]) and non-US denies cannot be turned off by settings or approval.
- Do not tune rules to the fixtures alone; write a held-out fixture set first and report results on it separately.
- Owner decisions: if you need a decision from Can (the owner), do not ask Can and do not write a decision block yourself. Stop at that point and put the question in your final report to the coordinator: the options (A, B, ...), your recommendation and a one-line reason. Only the coordinator posts owner-decision blocks.

## DONE
- Test output for `tests/l2/`, including every destination-gate rejection shape from S2 section 6.
- Accuracy report (train and held-out) in `docs/lanes/L2.md`, labelled "recorded hold simulation".
- Final report: commit SHA, test counts, false-human rate on held-out, open risks.
