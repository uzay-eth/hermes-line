# L2 round 3 (coordinator)

`git merge main`. Two policy changes, both coordinator decisions:
1. Toll-free numbers (+1 800, 833, 844, 855, 866, 877, 888) must be dialable. The core use case is airline, utility
   and bank hotlines. Keep refusing premium and pay-per-call (+1 900, 976), emergency, N11 and international premium.
   The allowlist mode still wins when it is on (the trial uses it). Tests both ways.
2. The hard-coded Bora number leaves `destinations.py`. Read a `blocked_destinations` list from policy config
   (default empty) and refuse those exactly, before any provider call. L1 adds the config plumbing; keep the policy
   API so L1 only passes the list. Tests: the list blocks, an empty list blocks nothing extra, normalisation
   (spaces, dashes, +1) applies.
Same LIMITS. DONE: test output, SHA.
