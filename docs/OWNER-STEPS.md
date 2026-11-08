# Owner steps (Can), one sitting

Kept by the coordinator. Last update 2026-11-05.

1. **Pane:** Vezir's round-3 notes went to L3. Look at the round-4 screenshots when they arrive; notes only if something is still off.
2. **xAI:** an xAI API key (through Vezir's secure path, never in chat) and approval of an xAI budget up to $2 for the live Grok tests (`docs/lanes/L5-live-test-plan.md`, about $1.54).
3. **Fi forwarding** (Fi app or fi.google.com; source: https://support.google.com/fi/answer/6201380):
   1. Home tab → Phone settings → **Call forwarding**.
   2. **Add a number:** the Twilio number of the line (Vezir has it).
   3. Verify it by **SMS code**. The code goes to the Twilio number, not the Pixel; the coordinator reads it through the Twilio API and sends it to Vezir right away. Tell Vezir the moment you tap "send code".
   4. Leave the preference on **Don't forward** for now and **Save**.
   5. When the coordinator says the callback route is live: Call forwarding → **Always forward** → the line's number → **Save**. Then call the Fi number from Spectrum once (Hermes should answer) and send one text to it (it should still arrive on the Pixel).
   - Keep "Only allow calls & texts from your phone contacts" **off**.
   - Undo any time: Call forwarding → **Don't forward** → Save.
4. **SMS code trial:** the Pixel is NOT connected to the Mac right now (adb sees no device, 2026-11-05 18:47Z). Plug it into the Studio by USB, unlock it and tap "Always allow from this computer". Then, while it stays on USB, ask any service you use to text you a login code, at a time the coordinator gives. This measures Android 17's code delay.
4b. **Owner line PIN:** choose a 4-8 digit PIN for calling your own Hermes line and have Vezir stage it at `~/.hermes-line/owner_pin` (one line, never in chat). Caller ID alone never unlocks owner mode.
5. **Demo:** about 20 minutes to record the demo (phone screen plus one real hold call), time to be agreed.
6. **Demo choices:** who plays the business. Recommended: a friend on the Spectrum line, labelled as staged, with a hold recording (fits the allowlist and recording consent). The filmed call says "this call is being recorded for a demo" instead of "not recorded".
7. **License and author name** for the public repo. Recommended: MIT, author "Can Uzay".
8. **Publishing (Nov 10-11, your decision each):** public GitHub repo, the catalog PR, the X post tagging @NousResearch, the submission form.

Not needed any more: Twilio Console, Auth Token, AI/ML addendum (the first live call ran on ConversationRelay without it). Recovering the Console email is optional.

## Status 2026-11-05 (coordinator)
- Done by Can: step 2 (xAI key staged, budget $2 approved); step 3.1-3.4 (Fi forwarding number added and verified, preference Don't forward).
- Finding: Twilio numbers do not receive OTP texts (error 30038). Codes come only from the Pixel. README updated.
