# ADR 0001: Project setup and Jev decision points

Status: accepted (Kodcu, 2026-11-04)

## Context
Hermes Line is a catalog plugin for the Nous Hermes Agent Plugin Hackathon (brief: docs/BRIEF.md). It must run on
any user's own Hermes, own Twilio account and optional Android phone.

## Decision
- Local git only (no remote) until Can decides on the public repo.
- Graft wired project-local (`--no-global`), `--deep` off.
- Work runs as parallel bb workers, one writer per worktree; the coordinator merges worker SHAs with `--no-ff`.

## Jev decision points
The shipped plugin cannot depend on TypeSafe/Jev: catalog users do not have a TypeSafe key, and the brief forbids new
paid services. Semantic decisions in the product (live human vs hold message vs voicemail, IVR option choice,
callback-to-task matching, OTP-to-task matching) run on the user's own Hermes model or deterministic rules. Each is
listed here with its threshold and low-confidence path once the spikes measure them on real or recorded examples:

| Decision point | Type | Options | Engine | Threshold | Low-confidence path | Log |
|---|---|---|---|---|---|---|
| Human answered after hold | choice | human / hold-message / ivr / voicemail | TBD (spike S2/S1) | TBD, measured | keep listening, re-check next utterance | call event log |
| Callback belongs to task | choice | open task ids / none | TBD | TBD | ask the owner | call event log |
| SMS code belongs to task | yes/no | yes / no | deterministic sender + time window first | n/a | do not release the code | masked OTP log |
