"""Send one OutgoingCallerIds validation request for the Fi line. Coordinator only, after VERIFY-GO.

Prints the 6-digit validation code once to stdout (it must reach the owner); evidence file stores it masked.
"""
import datetime
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(__file__))
import twilio_ops as t  # noqa: E402

FI = None


def main():
    global FI
    try:
        FI = t.initialize_numbers()["fi"]
    except ValueError as exc:
        sys.exit(str(exc))
    assert FI in t.ALLOW and FI != t.BORA, "refused"
    b0 = t.balance()
    assert b0[0] == 200 and b0[1][0] >= t.STOP_BALANCE, f"balance stop: {b0}"
    url = t.API + "/OutgoingCallerIds.json"
    sent = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    s, body = t.req("POST", url, {"PhoneNumber": FI, "FriendlyName": "Hermes Line Fi", "CallDelay": "60"})
    code = body.get("validation_code") if isinstance(body, dict) else None
    print(f"CODE {code} SENT {sent}")
    ev = {"sent_utc": sent, "http": s, "to": t.mask(FI), "call_sid_present": bool(body.get("call_sid")),
          "validation_code": "[CODE]", "balance_before": b0[1][0], "error": None if s in (200, 201) else body}
    os.makedirs("evidence/twilio", exist_ok=True)
    with open("evidence/twilio/03-verify-request.json", "w") as f:
        json.dump(ev, f, indent=1, default=str)


if __name__ == "__main__":
    main()
