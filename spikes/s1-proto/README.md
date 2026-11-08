# Hermes Line S1 offline probe

One no-op tool, one bundled skill, and an opt-in loopback HTTP/WebSocket server.
This is an architecture experiment, not the shipping phone plugin.

Disclosures: a daemon thread runs while the enabled plugin is loaded when
`settings.start_server: true`; it binds only 127.0.0.1. No external requests,
telemetry, credentials, phone actions, or persistent transcript storage. The
`/webhook` endpoint refuses every request. `/relay` accepts a small offline
ConversationRelay-shaped prompt/text exchange. It has no production authentication,
barge-in, reconnection or multi-call support. Do not expose it through a tunnel.

`benchmark.py` uses the real Hermes AIAgent with a deterministic localhost model
fixture and a fake calendar tool. Its numbers measure local harness overhead,
not model intelligence or phone latency. All state belongs in the S1 scratch home.
`run.py` selects existing Hermes dependencies read-only; it installs nothing.

The plugin runtime uses only Python's standard library. The measurement scripts
use aiohttp already present in the pinned Hermes environment; it is a harness
dependency, not a runtime dependency. The tiny WebSocket implementation accepts
only complete masked text frames plus ping/close, bounded at 16 KiB; shipping code
must use a maintained server library. The benchmark's tool-search deferral is off
only in scratch so the fixture can name its fake calendar tool directly.

On this Mac, after scratch preparation described in the spike report:

```sh
sh spikes/s1-proto/offline.sh spikes/s1-proto/lifecycle.py
sh spikes/s1-proto/offline.sh spikes/s1-proto/benchmark.py
sh spikes/s1-proto/offline.sh hermes plugins validate spikes/s1-proto --install-deps
```

`run.py` and `offline.sh` are reproducibility harnesses, not a distributable Hermes
launcher. They name the measured Mac's installed Python/dependency generation.
Do not include these harnesses or evidence in the shipping catalog package.
