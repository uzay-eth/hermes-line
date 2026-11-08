# Recorded hold simulation

The original self-authored English streams are CC0-1.0; no audio or downloaded
media is included. `train.json` has 30 short streams. `held-out.json` retains
its original 30 unchanged streams and adds two masked transcript replays from
`evidence/trial/call_68921278eab742c6/trial.json` (32 total). These two cases
are derived from coordinator-supplied live-call evidence; they are labelled
simulation because this lane replays text only and makes no call. Owner and
speaker names are replaced by `[OWNER]` and `[NAME]`. Timing is relative to
the preceding Hermes turn (8595 ms greeting; 9739 ms confirmation reply).

The original streams include five each of silence, hold recordings, IVR,
voicemail, live humans and ambiguous mixtures. Original held-out utterances
were authored before round 1; the two live regressions were added and scored
with the old detector before round 2 changes. Minimal hello and silence
confusers intentionally overlap splits. No speaker/accent recognition is tested.

`soak.json` contains six 35-minute virtual scripts (3.5 hours): repeated prompts,
silence, speech over simulated music, recorded hello, a late human, and
reconnect/replay. “Music” is scenario metadata: no acoustic features are
available to this detector. The speech-over-music script includes a human
arriving mid-message. Partial/final and replay behavior also has unit coverage.

`at_ms` is elapsed transcript time; `human_onset_ms` is authored recognized
speech onset, not acoustic onset. Events include type, stable turn ID, speaker,
text, finality, hidden ground-truth label and permissible actions. The evaluator
passes only Utterance fields to HoldTracker, never labels/actions/onset.
System socket events are retained in the script but are not transcript input.
An actual adapter must supply a stable event ID and preserve call liveness.

Scores count final business transcript events, including the duplicate human
turn in the replay scenario (which correctly emits UNKNOWN again). Episode
misses count a stream with human onset and no confirmed HUMAN event. Genuine
humans who say only hello remain human ground truth, so abstention is reported
as a miss. Silence maps to UNKNOWN because the contract has no hold_music label.
Negative exposure is authored pre-human time; it includes silence/IVR/voicemail,
not only queue announcements. No owner ring action is executed; false HUMAN
is a proxy for false-ring risk. Model-call counts exclude deterministic rules
and cached repeats. Cache and window bounds have unit coverage; the virtual
soak is not a real-time watchdog or network survival measurement.

Reproduce from repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=plugin python3 -m unittest discover -s tests/l2 -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=plugin python3 tests/l2/evaluate.py
```

The fake model has explicit canned contingent answers in `tests/l2/evaluate.py`.
It receives no ground-truth labels. Its success measures adapter/gating behavior,
not semantic generalization or actual Hermes quality. The authored scripts and two related live replays remain a small, correlated corpus. Missing acoustic/STT evidence, a real-time soak,
and actual Hermes inference remain unvalidated.

Round 2 before/after replay (local git only, baseline c88f182):

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=plugin python3 tests/l2/compare_round2.py
```

Archived results: `tests/l2/round2-before.json`, `tests/l2/round2-after.json`;
full test output: `tests/l2/round2-test-output.txt`. The fake model is unchanged.
