# L1 round 12 (coordinator): findings from live calls 2 and 3

`git merge main`. Evidence: `evidence/trial/call_9fda11b071df4fea/trial.json` (outbound, owner call 2, 43 s) and
`evidence/trial/call_c20d76d32e9e47db/trial.json` (inbound via Fi Always forward, owner call 3, 92 s). Service stderr in
`~/.hermes-line/trial-logs/` (do not copy into the repo).
Facts: Fi forwarding delivered `From` = the original caller (the owner's Spectrum) and no `ForwardedFrom`; the event
says `caller_hidden: true, forwarded: false`. Silence fix holds: fillers at 2.5 s and 5 s covered every slow turn.
Fix, with theories first where the cause is not obvious:
1. The owner calling the line from the owner number was treated as an unknown caller "Ken". A call from
   `owner_number` must be recognised as the owner (owner greeting, owner-only actions still need the usual consent),
   and must not be labelled `caller_hidden`. Detect Fi forwarding without `ForwardedFrom` (To = line, configured
   `forwarding_number`), and record it as forwarded when you can tell.
2. ConversationRelay `hints` were evidently not used on the inbound call ("Can" heard as "Ken" twice); apply hints
   (owner name, business names of open tasks) on inbound too.
3. Latency: inbound mid-call turns had first text 3.6-5.8 s (outbound 1.6-2.0 s), and every finish turn 5.7-7.3 s
   (tool search, then the tool). Write 3-4 theories (inbound briefing size, no warm-up, tool search on every finish,
   barge-in restarts) and test them; target first text under 2.5 s on normal turns and a finish that does not need a
   tool search step (for example warm the tool schema at session start, if Hermes allows it).
4. Copy: after the disclosure greeting, Hermes repeated "I'm Hermes, an AI assistant" twice more in the outbound
   call. Disclose once per call unless asked.
Same LIMITS. DONE: theories + evidence, test output, SHA.
