# Forward the Google Fi line to the Hermes Line Twilio number

For: Can (owner). Written by lane L7 on 2026-11-05 from Google's and Twilio's public help pages, all read on
2026-11-05. Nothing here was tried on the phone or the Fi account; every step below is the owner's.

Goal: a business that got a call from Hermes (caller ID = the Fi line, [owner phone (Fi)], verified on Twilio on
2026-11-05, `evidence/twilio/04-verify-check.json`) calls that number back, Fi forwards the call to the plugin's
Twilio number (Reno NV, bought 2026-11-05, `evidence/twilio/02-buy.txt`; the full number is in
`~/.hermes-line/twilio_line_number`, not in this repo), and Hermes answers it.

## Before you turn it on

Do not turn forwarding on until all three are true. Otherwise callers reach a number that nobody answers.

1. The plugin's inbound route for callbacks is merged and tested (lane L1 round 7, `docs/briefs/L1-round7-live-findings.md`
   section 2). As of commit `c88f182` it is briefed, not built.
2. The coordinator has pointed the Twilio number's voice webhook at the running plugin. On 2026-11-05 the number had
   no webhook (`evidence/twilio/02-buy.txt`: `voice_url: None`, `sms_url: ''`).
3. The plugin service and its public tunnel are running. With "Always forward", every call to the Fi line goes to
   Twilio, and the Pixel does not ring. If the plugin is down, callers do not reach Fi voicemail.

## Which forwarding modes Fi offers

From [Call forwarding & caller ID](https://support.google.com/fi/answer/6201380?hl=en) (Google Fi Help), the only
modes are:

> **Don't forward:** Only your primary phone number will ring. Your forwarding numbers won't ring.
> **Ring all numbers:** Your primary and selected forwarding numbers will ring at the same time.
> **Always forward:** The call will always be forwarded to your selected number and your primary number won't ring.

> For the "Always forward" option, you can only select one forwarding number.

Fi's page documents no forwarding "when busy", "when not answered" or "when unreachable". The page also says:

> Call forwarding with Google Fi must be set up on fi.google.com or within your Google Fi app, not within your
> Android or iPhone's device settings.

So do not use the Pixel's dialer settings or `*` / `#` codes for this.

**Use "Always forward".** With "Ring all numbers", the Pixel and Twilio ring at the same time, so whether Hermes or
the Pixel takes a callback depends on who answers first. "Always forward" makes every callback reach Hermes.

## Steps (turn it on)

Quoted steps are from [support.google.com/fi/answer/6201380](https://support.google.com/fi/answer/6201380?hl=en).

1. Open the Google Fi website ([fi.google.com](https://fi.google.com)) or the Google Fi app on the Pixel.
2. In the "Home" tab, under "Phone settings", select **Call forwarding**.
3. Select **Add a number** and enter the plugin's Twilio number.
4. Verify the number. Google's page: "You'll be asked to verify the number. Follow the onscreen steps to complete
   verification." It offers a code by SMS, or "verify by call" as an alternative. The code goes **to the Twilio
   number**, not to the Pixel, so you need a way to read it there (see "Verifying a Twilio number" below).
5. Choose the preference **Always forward** and pick the Twilio number as the forwarding number.
6. Click **Save**. Google's page: "Click Save to ensure your selection is applied."
7. Test: from the Spectrum phone, call the Fi number. Expected: the Pixel does not ring, and the plugin answers
   with its disclosure greeting. The coordinator then checks the Twilio inbound request for `From` and
   `ForwardedFrom` (see "Caller ID of forwarded calls").
8. Test SMS: send a text to the Fi number from the Spectrum phone. Expected: it arrives on the Pixel as before.

## Verifying a Twilio number (step 4)

The number has no SMS or voice webhook today, and the Twilio Console is not reachable for the owner
(STATUS.md, owner updates 2026-11-05). Options:

- **A. SMS code, read through the Twilio API (recommended).** Choose SMS verification on the Fi screen. The
  coordinator lists the newest inbound message to the line with the existing API key and passes
  the code to Can privately, masked in every log, as was done for the caller-ID verification
  (`evidence/twilio/03-verify-request.json` shows `"validation_code": "[CODE]"`). UNCONFIRMED: that Twilio
  keeps an inbound SMS in the message log when the number has no SMS webhook. The number has SMS capability
  (`evidence/twilio/02-buy.txt`: `'sms': True`). Twilio's inbound SMS price was not checked.
- **B. "Verify by call".** Fi calls the Twilio number and speaks a code. Someone has to hear it, so the number's
  voice webhook would first have to point at something that bridges the call to the owner. That needs the
  callback route from L1 round 7 or a Twilio configuration change. Use only if A fails.

## Turn it off (undo)

From the same page:

1. Open the Google Fi website or app.
2. In the "Home" tab, under "Phone settings", select **Call forwarding**.
3. Choose **Don't forward** ("Only your primary phone number will ring").
4. Click **Save**.
5. Optional, to remove the Twilio number completely: select the pencil icon next to it, then **Remove**.

Turn forwarding off first whenever Twilio has to place a caller-ID verification call to the Fi line again: the
Pixel must ring for that call (`docs/spikes/S2-telephony.md` section 4).

## What forwarding does to SMS

Fi's forwarding page describes forwarding of calls only ("You can forward the calls you receive to a separate
number") and says nothing about texts. No Fi help page read for this note says that texts are forwarded. Texts
should therefore keep arriving on the Pixel, where the plugin reads them over USB (`evidence/sms/probe-check.txt`:
`"READ_SMS query accepted"` on the Pixel 10 Pro XL, Android 17). This is not stated outright by Google:
confirm it with step 8 above after turning forwarding on.

Two Fi settings can stop texts or callbacks before forwarding matters:

- **"Only allow calls & texts from your phone contacts" must stay off.** From
  [support.google.com/fi/answer/12982560](https://support.google.com/fi/answer/12982560?hl=en): "calls from
  numbers not in your contact list go straight to voicemail and texts are blocked." Its exception ("numbers you
  contact via call or text can reply back for a month") covers calls placed from the Fi phone. Hermes places
  calls through Twilio, so Fi does not see them as contacts. The page also says "Text messages from short-codes
  are automatically allowed", which covers many verification texts, but not codes sent from a full-length number.
- **"Block known spam"** ([support.google.com/fi/answer/9016293](https://support.google.com/fi/answer/9016293?hl=en)):
  spam calls "don't ring on your phone". Whether a call Fi labels as spam is still forwarded is not stated. Leave
  the setting as it is and watch for missed callbacks.

RCS: Fi's texting page ([support.google.com/fi/answer/6205096](https://support.google.com/fi/answer/6205096?hl=en))
says Google Messages can carry messages over RCS. The plugin reads the SMS inbox (`content://sms/inbox`).
Whether a code that arrives as an RCS message appears there was not tested; SMS codes from short codes are what
the probe saw (`evidence/sms/probe-inbox.txt`, sender `95737`).

## Caller ID of forwarded calls

- Google's forwarding page says nothing about the caller ID a forwarded call carries.
- Twilio's request parameters ([TwiML for Programmable Voice](https://www.twilio.com/docs/voice/twiml), section
  "Request parameters"): "ForwardedFrom: This parameter is set only when Twilio receives a forwarded call, but its
  value depends on the caller's carrier including information when forwarding. Not all carriers support passing
  this information."
- So it is UNCONFIRMED whether `From` on a forwarded call is the business's number (needed to match a callback to
  its task) and whether `ForwardedFrom` shows the Fi number. Step 7's test answers both. If `From` turns out to
  be the Fi number or empty, callbacks cannot be matched by number; the plugin then treats every callback as
  unknown (disclosure, take a message or ring the owner), as L1 round 7 specifies.
- Loop rule: the Twilio number must never forward or dial back to the Fi line, and the owner's bridge phone is
  the Spectrum line, not the Fi line (`docs/spikes/S2-telephony.md` section 4).

## Sources (read 2026-11-05)

- Google Fi Help, [Call forwarding & caller ID](https://support.google.com/fi/answer/6201380?hl=en)
- Google Fi Help, [Only allow calls & texts from your phone contacts on Google Fi](https://support.google.com/fi/answer/12982560?hl=en)
- Google Fi Help, [Block spam calls](https://support.google.com/fi/answer/9016293?hl=en)
- Google Fi Help, [Send & receive text messages (SMS, MMS & RCS)](https://support.google.com/fi/answer/6205096?hl=en)
- Twilio Docs, [TwiML for Programmable Voice, request parameters](https://www.twilio.com/docs/voice/twiml)
