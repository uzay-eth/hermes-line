# Review R1b delta 25 (coordinator): new-party confirmation and unsafe-offer precedence

PURPOSE: Your delta 24 blocked the one Delta retry (N20, N21, N22). Both lanes answered; confirm before the retry.

TASK: Read-only review on `main` of:
1. L2 commit a33571d (`plugin/line/detect/`, `tests/l2/`, `docs/lanes/L2.md` round 7): `possible_new_party` and
   `transfer` markers, boundaries, L2 as the single source of transfer recognition.
2. L1 commit bdb693a (`plugin/line/engine/`, `tests/l1/test_r27_new_party.py`, `docs/lanes/L1.md` sections 38-39):
   the bot latch comes only from L2 labels; a possible new party gets the fixed live-person question (20 s spacing,
   4 per call) and only a clear yes reaches the owner ring; unsafe offers are checked before data words.
Re-run your delta-24 sequences (your `review.py` uses the removed `_track_assistant`; adapt it to the public
engine path) and Delta run 1 (`evidence/trial/call_b669b105d4774a49/trial.json`). Try a bot that answers "yes" to the
live-person question: the owner must still press 1 to be bridged, so state whether that residual is acceptable.
Known: one L5 rehearsal test is being updated for the round-6 contract; ignore it.

LIMITS:
- Read only in the repo. Write only under `/Users/mac/.hermes/cache/scratch/hermes-line-r1d25/`.
- No network, no calls, no provider access. Read only `~/.hermes-line/approved_destinations.json` under that folder.
- Do not commit or push.

DONE: `REVIEW-DELTA25.md` there with findings and one verdict for the Delta retry: yes or no.
