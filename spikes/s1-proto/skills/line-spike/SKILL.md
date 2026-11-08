---
name: line-spike
description: Explain the offline Hermes Line architecture probe and its no-op tool.
---
Use hermes_line_noop only to inspect this offline probe. It cannot call a phone,
release an OTP, contact Twilio, or provide calendar access. The loopback fake relay
is a test fixture, not a production telephony endpoint.
