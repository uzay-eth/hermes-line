# B13 (coordinator, one round, cross-lane): strip Can's personal numbers from tracked files

PURPOSE: The repo may go public later (Can's decision). Can's own phone numbers must not be in tracked files. No
history rewrite and no new repo; this round only fixes the working tree on main.

TASK:
- The real numbers now live only in `~/.hermes-line/personal_numbers.json` (0600, keys `owner`, `fi`) and
  `~/.hermes-line/twilio_line_number`. Code that needs them reads them from there (one small loader, e.g. in
  `tools/trial/` and reused by `tools/coordinator/`), and fails with a clear message if the file is missing.
  Affected code: `tools/trial/run.py` (ALLOW and any other use), `tools/coordinator/twilio_ops.py`,
  `tools/coordinator/verify_caller_id.py`, `tools/coordinator/send_latency_probe.py`. Behaviour stays the same:
  the allowlist is exactly the owner and Fi numbers, the probe still refuses any number other than Fi.
- Tests: replace real numbers with fictitious ones (+1 555 01xx style or the existing fake-number convention in the
  suite). Where a test checks the harness allowlist, point the loader at a temp fixture file instead of the real one.
  Files: `tests/l2/test_policy.py`, `tests/l4/test_latency_probe.py`, `tests/l6/test_gate_errors.py`,
  `tests/l6/test_harness.py`, `tests/l6/test_profile_routing.py`.
- Docs and evidence: replace each real number with `[NUMBER ending NNNN]` (the last four digits only). Files:
  `STATUS.md`, `docs/BRIEF.md`, `docs/briefs/L6-trial-harness.md`, `docs/lanes/L4.md`, `docs/lanes/L6.md`,
  `docs/release/fi-forwarding.md`, `tools/trial/README.md`, `evidence/round-latest-*.json`, `evidence/round2-*.json`,
  `evidence/start-*.json`.
- `docs/BRIEF.md` changes, so its sha256 changes. Update the hash in `AGENTS.md` and `CLAUDE.md` (project rules
  line) to the new value and keep the old one in a note: "(before B13 masking: 346bbac7…)".
- Add `tools/coordinator/scan_personal_numbers.py`: reads the real numbers from `~/.hermes-line`, matches every
  common formatting, and prints only file paths and counts. Add an offline test for it with a fixture file. Use it to
  prove zero hits.

LIMITS:
- Never print, log or commit a real number. Use the scanner; when you need to see a match, print the masked form.
- You may edit every file listed above, nothing else outside your usual paths unless the scanner finds it.
- No history rewrite, no push, no new repo, no network, no calls.
- Do not edit `~/.hermes-line/` files; read only.

DONE: scanner output with 0 files, all suites green (L1 in parts if needed, L2, L3, L4, L5, L6, README sync check),
the commit SHA, and the new BRIEF hash.
