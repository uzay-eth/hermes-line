# L5 round 5 (coordinator): R1 delta 7 findings

`git merge main`. Read `evidence/reviews/R1-DELTA7-voice.md` and the probes under
`/Users/mac/.hermes/cache/scratch/hermes-line-r1d7/fixtures/` (read only; copy into tests/l5 if useful).
- N9: a cancelled Grok (and STT) setup must close its socket. Track the connection from the moment the handshake
  returns, close it on cancel, error or `VoiceCall.close`, and make `close` wait for it. Test with the reviewer's
  `probe_voice.py` case and the same for STT; zero open sockets after close.
- N10: no Authorization header may reach any log at any level. Pass a private logger (or a redacting filter) to the
  websockets client for both `grok.py` and `stt.py`; test with root logging at DEBUG that the key appears in no
  log record, and also in no exception text on a failed handshake.
Fix anything else the review lists as must-fix for the trial. Same LIMITS. DONE: test output (l1, l5), SHA.
