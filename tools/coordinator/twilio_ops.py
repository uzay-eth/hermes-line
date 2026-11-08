#!/usr/bin/env python3
"""Coordinator-only Twilio ops for the Hermes Line dogfood (not part of the plugin).

Reads credentials from ~/.hermes-line (never prints them). Prints only non-secret facts.
Enforces: dial allowlist, spend floor, Bora number never touched.
"""
import base64
import json
import os
from pathlib import Path
import sys
import urllib.parse
import urllib.request
import urllib.error

HOME = os.path.expanduser("~/.hermes-line")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.trial.personal_numbers import load_personal_numbers, load_line_number
ALLOW = set()


def initialize_numbers(path=None):
    global ALLOW
    numbers = load_personal_numbers(path)
    ALLOW = {numbers['owner'], numbers['fi']}
    return numbers


BORA = "+17755550172"  # the author's protected number; a fictional 555 value in the public copy
STOP_BALANCE = 4.00


def _creds():
    sid = open(os.path.join(HOME, "twilio_account_sid")).read().strip()
    k = json.load(open(os.path.join(HOME, "twilio_api_key.json")))
    return sid, k["api_key_sid"], k["api_key_secret"]


def req(method, url, data=None):
    sid, ksid, ksec = _creds()
    url = url.replace("{AC}", sid)
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=body, method=method)
    r.add_header("Authorization", "Basic " + base64.b64encode(f"{ksid}:{ksec}".encode()).decode())
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read())
        except Exception:
            j = {}
        return e.code, {"code": j.get("code"), "message": j.get("message"), "more_info": j.get("more_info")}


API = "https://api.twilio.com/2010-04-01/Accounts/{AC}"


def balance():
    s, j = req("GET", API + "/Balance.json")
    return s, (float(j["balance"]), j.get("currency")) if s == 200 else j


def mask(n):
    return n[:5] + "***" + n[-4:] if n else n


if __name__ == "__main__":
    try:
        initialize_numbers()
    except ValueError as exc:
        sys.exit(str(exc))
    cmd = sys.argv[1]
    if cmd == "balance":
        print("BALANCE", balance())
    elif cmd == "numbers":
        s, j = req("GET", API + "/IncomingPhoneNumbers.json?PageSize=50")
        print("STATUS", s)
        for n in j.get("incoming_phone_numbers", []):
            print("NUMBER", mask(n["phone_number"]), n["sid"][:6] + "...", "voice_url_set", bool(n.get("voice_url")),
                  "voice_app", bool(n.get("voice_application_sid")), "trunk", bool(n.get("trunk_sid")))
    elif cmd == "callerids":
        s, j = req("GET", API + "/OutgoingCallerIds.json")
        print("STATUS", s, [mask(c["phone_number"]) for c in j.get("outgoing_caller_ids", [])] if s == 200 else j)
    elif cmd == "price":
        s, j = req("GET", "https://pricing.twilio.com/v1/PhoneNumbers/Countries/US")
        print("STATUS", s, [(p.get("number_type"), p.get("base_price"), p.get("current_price")) for p in j.get("phone_number_prices", [])] if s == 200 else j)
    elif cmd == "search":
        s, j = req("GET", API + "/AvailablePhoneNumbers/US/Local.json?AreaCode=775&VoiceEnabled=true&SmsEnabled=true&PageSize=5")
        print("STATUS", s)
        for n in j.get("available_phone_numbers", []) if s == 200 else [j]:
            print("AVAIL", mask(n.get("phone_number")), n.get("locality"), n.get("region"), n.get("capabilities"))
    elif cmd == "buy":
        num = sys.argv[2]
        assert num != BORA and num.startswith("+1775"), "refused"
        s, b = balance()
        assert s == 200 and b[0] - 1.15 >= STOP_BALANCE, f"balance stop: {b}"
        s, j = req("POST", API + "/IncomingPhoneNumbers.json", {"PhoneNumber": num, "FriendlyName": "Hermes Line (dogfood)"})
        print("STATUS", s, {k: mask(j.get(k)) if k == "phone_number" else j.get(k) for k in ("phone_number", "date_created", "status", "capabilities", "code", "message")},
              "sid_prefix", (j.get("sid") or "")[:6])
        print("BALANCE_AFTER", balance())
    else:
        sys.exit("unknown command")
