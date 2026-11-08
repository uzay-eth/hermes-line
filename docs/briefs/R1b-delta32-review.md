# Review R1b delta 32 (coordinator): utterance start stamp (L1 round 34)

PURPOSE: Your delta 31 blocked Delta run 3 with one finding. L1 answered; this is the check before asking the owner.

TASK: Read-only review on `main` of L1 commit f71f800 (`plugin/line/engine/machine.py` `SpokenUtterance` stamp,
`tests/l1/test_r34_utterance_stamp.py`, `docs/lanes/L1.md` section 46) against `evidence/reviews/R1-DELTA31.md`.
Rule: only an utterance that started after the send time of the latest live-person question can confirm, on every
route. Re-run your delta-31, 30, 29 and 28 probes (your `r33.py` reads the removed `predates_question`; adapt it)
and Delta runs 1 and 2.

LIMITS:
- Read only in the repo. Write only under `/Users/mac/.hermes/cache/scratch/hermes-line-r1d32/`.
- No network, no calls, no provider access. Read only `~/.hermes-line/approved_destinations.json` under that folder.
- Do not commit or push.

DONE: `REVIEW-DELTA32.md` there with findings and one verdict for Delta run 3: yes or no.
