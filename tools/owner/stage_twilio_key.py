#!/usr/bin/env python3
"""Owner one-shot: copy the Twilio API key from 1Password into a private file for Hermes Line.

Run once in your own Terminal (it needs 1Password's approval there):
    python3 /Users/mac/Projects/hermes-line/tools/owner/stage_twilio_key.py

It writes ~/.hermes-line/twilio_api_key.json (mode 0600, dir 0700) and prints only non-secret facts.
It never overwrites an existing file. No value is printed, logged or passed on a command line.
"""
import json
import os
import re
import subprocess
import sys

ITEM = os.environ.get("HL_OP_ITEM", "Twilio Bora API Credentials")
DEST_DIR = os.path.expanduser(os.environ.get("HL_DEST_DIR", "~/.hermes-line"))
DEST = os.path.join(DEST_DIR, "twilio_api_key.json")
SK_RE = re.compile(r"SK[0-9a-f]{32}")
SECRET_RE = re.compile(r"[A-Za-z0-9]{32}")


def fail(msg):
    print("STAGE_OK=false")
    print("REASON=" + msg)
    sys.exit(1)


def main():
    if os.path.lexists(DEST):
        fail("destination exists; not overwriting")
    try:
        out = subprocess.run(["op", "item", "get", ITEM, "--format", "json"],
                             capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        fail("op CLI not found")
    except subprocess.TimeoutExpired:
        fail("1Password did not answer (locked or prompt not approved)")
    if out.returncode != 0:
        fail("op failed: " + (out.stderr.strip().splitlines() or ["unknown"])[0][:160])
    try:
        item = json.loads(out.stdout)
    except ValueError:
        fail("op output was not JSON")
    values = [(f.get("id"), f.get("label"), f.get("type"), f.get("value") or "") for f in item.get("fields", [])]
    sks = [v for v in values if SK_RE.fullmatch(v[3])]
    secrets = [v for v in values if v[2] == "CONCEALED" and SECRET_RE.fullmatch(v[3]) and not SK_RE.fullmatch(v[3])]
    for fid, label, ftype, val in values:
        print("FIELD id=%s label=%s type=%s has_value=%s sk_shape=%s secret_shape=%s" % (
            fid, label, ftype, bool(val), bool(SK_RE.fullmatch(val)),
            ftype == "CONCEALED" and bool(SECRET_RE.fullmatch(val))))
    if len(sks) != 1:
        fail("expected exactly one SK... key sid field, found %d" % len(sks))
    if len(secrets) != 1:
        fail("expected exactly one 32-char concealed secret field, found %d" % len(secrets))
    os.makedirs(DEST_DIR, mode=0o700, exist_ok=True)
    os.chmod(DEST_DIR, 0o700)
    old = os.umask(0o077)
    try:
        fd = os.open(DEST, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    finally:
        os.umask(old)
    with os.fdopen(fd, "w") as fh:
        json.dump({"api_key_sid": sks[0][3], "api_key_secret": secrets[0][3]}, fh)
    print("STAGE_OK=true")
    print("DEST=" + DEST)
    print("MODE=" + oct(os.stat(DEST).st_mode & 0o777))


if __name__ == "__main__":
    main()
