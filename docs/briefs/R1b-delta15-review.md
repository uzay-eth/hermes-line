# Review R1 delta 15 (coordinator): menu output filter conformance (L1 round 18)

PURPOSE: Hermes Line is a phone plugin that calls businesses for its owner. Before a supervised test call to an
airline reservations line, an independent reviewer must confirm the new menu output filter behaves as specified, so
the call cannot agree to anything the owner did not ask for. You continue the work of an earlier reviewer whose
session ended; their notes and scripts are in the scratch folder below.

TASK: Read-only conformance review of `plugin/line/engine/menu_gate.py` and the stale-turn handling in L1 commit
7c19356 on `main` in `/Users/mac/Projects/hermes-line` (`tests/l1/test_r18_menu_gate.py`, `docs/lanes/L1.md` latest
section). Earlier findings it should fix: `evidence/reviews/R1-DELTA13.md` (N16, N17). The spec: on restricted or
information-only tasks, only navigation to reservations, baggage or general information, staying in the queue,
declining, and asking for an agent may be sent, by voice or keypad; offers to call back, payments, charges and booking
changes are always declined; replies prepared before a person answered are never spoken after.
Write table-driven cases for menu wordings a real airline line might use, including offers to call back that do not
use the word "callback" (for example "we can call you back", "keep your place in line"), menus that list a safe and
an unsafe option under one key, multi-key entries, the star key, numbers spoken as words, and a prepared reply that
completes while the owner accepts the bridge. Run them against the real code with fakes. Reuse
`/Users/mac/.hermes/cache/scratch/hermes-line-r1d15/` (earlier notes, `run_checks.py`, `variants.log`).

LIMITS:
- Read only in the repo. Write only under `/Users/mac/.hermes/cache/scratch/hermes-line-r1d15/`.
- No network, no calls, no provider access. Do not read `~/.hermes-line/`.
- Do not commit or push.

DONE: `REVIEW-DELTA15.md` in that folder with each case and its result, findings with severity, and a verdict for the
airline hold test, engine side: yes or no.
