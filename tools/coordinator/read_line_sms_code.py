"""Poll inbound SMS to the plugin's Twilio line and print the newest verification code once. Coordinator only.

Used for the Fi forwarding verification (docs/release/fi-forwarding.md). Prints only the code and the message time;
writes masked evidence. Usage: python3 tools/coordinator/read_line_sms_code.py [--minutes 10] [--wait 300]
"""
import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(__file__))
import twilio_ops as t  # noqa: E402


def newest(line, since):
    q = urllib.parse.urlencode({"To": line, "PageSize": 20})
    s, j = t.req("GET", t.API + "/Messages.json?" + q)
    if s != 200:
        raise SystemExit(f"messages read failed: {s} {j}")
    for m in j.get("messages", []):
        if m.get("direction") != "inbound":
            continue
        sent = datetime.datetime.strptime(m["date_created"], "%a, %d %b %Y %H:%M:%S %z")
        if sent < since:
            continue
        code = re.search(r"(?<!\d)(\d{4,8})(?!\d)", m.get("body") or "")
        if code:
            return code.group(1), sent, m.get("from")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=10)
    ap.add_argument("--wait", type=int, default=300)
    a = ap.parse_args()
    line = open(os.path.join(t.HOME, "twilio_line_number")).read().strip()
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=a.minutes)
    end = time.time() + a.wait
    while True:
        hit = newest(line, since)
        if hit or time.time() > end:
            break
        time.sleep(5)
    if not hit:
        print("NO CODE")
        return
    code, sent, sender = hit
    print(f"CODE {code} RECEIVED {sent.astimezone(datetime.timezone.utc):%Y-%m-%dT%H:%M:%SZ}")
    os.makedirs("evidence/twilio", exist_ok=True)
    with open("evidence/twilio/05-fi-forward-verify-sms.json", "w") as f:
        json.dump({"received_utc": f"{sent:%Y-%m-%dT%H:%M:%SZ}", "from": t.mask(sender), "code": "[CODE]"}, f)


if __name__ == "__main__":
    main()
