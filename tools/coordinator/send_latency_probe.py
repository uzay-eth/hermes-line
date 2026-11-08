#!/usr/bin/env python3
"""Send ONE latency-probe SMS from the plugin's Twilio line to the owner's Fi phone. Coordinator only.

    python3 tools/coordinator/send_latency_probe.py --new-nonce                    # prints a fresh nonce; sends nothing
    python3 tools/coordinator/send_latency_probe.py --nonce N --dry-run            # shows the request; no credentials, no network
    python3 tools/coordinator/send_latency_probe.py --nonce N --i-am-the-coordinator   # sends the one message

The body is exactly "hermes-line latency probe <nonce>", the nonce 8-16 lowercase letters, so the text never carries
a code or a number. Pair it with `tests/l4/live/otp_delay.py --probe-nonce N`, started first, which reads the Pixel
and joins this script's send record with the phone arrival time and the plugin read time.

Refuses: any destination other than the Fi line (exact E.164, no normalisation), the Bora number anywhere, a sender
that is not the stored plugin line, a balance under the stop floor, a nonce that already has a send
record (the record is created exclusively BEFORE the request, so a rerun cannot send a second message), any
send while another probe was sent in the last 15 minutes, and (unless --wait-ready 0) any send before the reader
`otp_delay.py --probe-nonce <nonce>` has written its ready file and is still running. One POST, never retried. After it, read-only GETs of that
one message for up to --follow seconds record Twilio's status, error code and date_sent.

Credentials come from ~/.hermes-line via twilio_ops.py and are never printed or stored. The record
evidence/sms/latency-probe-<nonce>-send.json holds masked numbers, times, the message SID and the status.
Exit codes: 0 sent (delivered, or still in flight when --follow ran out), 2 refused, 6 Twilio refused or failed it.
"""
import argparse
import datetime
import email.utils
import glob
import json
import os
import re
import secrets
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import twilio_ops as t  # noqa: E402

PERSONAL_NUMBERS_PATH = None
FI = None


def initialize_numbers():
    global FI
    FI = t.initialize_numbers(PERSONAL_NUMBERS_PATH)["fi"]
    return FI


TEMPLATE = "hermes-line latency probe %s"
NONCE_RE = re.compile(r"[a-z]{8,16}")
SID_RE = re.compile(r"SM[0-9a-f]{32}")
FINAL = ("delivered", "undelivered", "failed", "canceled")
RECENT_S = 15 * 60
BALANCE_MARGIN = 0.10          # one SMS segment costs about a cent; keep well above the stop floor
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_OUT = os.path.join(_ROOT, "evidence", "sms")
_LETTERS = "bcdfghjkmnpqrstvwxz"   # no vowels: a nonce never spells a word


class Refused(Exception):
    pass


def new_nonce() -> str:
    return "".join(secrets.choice(_LETTERS) for _ in range(10))


def body_for(nonce: str) -> str:
    if not isinstance(nonce, str) or not NONCE_RE.fullmatch(nonce):
        raise Refused("the nonce must be 8-16 lowercase letters")
    return TEMPLATE % nonce


def check_to(to: str) -> str:
    try:
        initialize_numbers()
    except ValueError as exc:
        raise Refused(str(exc)) from None
    if to != FI or FI not in t.ALLOW or to == t.BORA:
        raise Refused("this helper only texts the Fi line %s" % t.mask(FI))
    return to


def line_number(home: str) -> str:
    try:
        line = t.load_line_number(os.path.join(home, "twilio_line_number"))
    except ValueError as exc:
        raise Refused(str(exc)) from None
    # The sender is the line the user configured (twilio_line_number); never a protected or personal number.
    if line in (t.BORA, FI) or line in t.ALLOW:
        raise Refused("the stored sender is not the plugin line")
    return line


def iso_ms(ms):
    if ms is None:
        return None
    return datetime.datetime.fromtimestamp(ms / 1000.0, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def rfc2822_ms(value):
    """Twilio's 'Thu, 05 Nov 2026 21:20:36 +0000' -> ms, or None."""
    if not value:
        return None
    try:
        return int(email.utils.parsedate_to_datetime(value).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def record_path(out_dir: str, nonce: str) -> str:
    return os.path.join(out_dir, "latency-probe-%s-send.json" % nonce)


def _write(path: str, doc: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def _recent_send(out_dir: str, now_ms: int):
    for p in glob.glob(os.path.join(out_dir, "latency-probe-*-send.json")):
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            return p            # unreadable: assume it may be a send in progress
        if d.get("dry_run"):
            continue
        if d.get("state") in ("reserved", "sent", "unknown_outcome") and now_ms - int(d.get("reserved_ms") or 0) < RECENT_S * 1000:
            return p
    return None


def _pid_alive(pid) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_ready(out_dir: str, nonce: str, wait_s: float, *, now=time.time, sleep=time.sleep, alive=_pid_alive):
    """Block until otp_delay.py --probe-nonce <nonce> says it is watching (its ready file, fresh, its process alive)."""
    path = os.path.join(out_dir, "latency-probe-%s-ready.json" % nonce)
    end = now() + wait_s
    while True:
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
            if (d.get("nonce") == nonce and now() * 1000 - int(d.get("ready_ms") or 0) < RECENT_S * 1000
                    and alive(d.get("pid"))):
                return d
        except (OSError, ValueError, TypeError):
            pass
        if now() >= end:
            raise Refused("the reader is not watching (start tests/l4/live/otp_delay.py --probe-nonce %s first)" % nonce)
        sleep(1.0)


def _reserve(path: str, doc: dict) -> None:
    """Create the send record exclusively: a second run for the same nonce fails here, before any request."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        raise Refused("nonce already used (%s exists): pick a new nonce" % os.path.basename(path))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")


def _twilio_fields(j: dict) -> dict:
    return {
        "status": j.get("status"), "error_code": j.get("error_code") or j.get("code"),
        "error_message": j.get("error_message") or j.get("message"),
        "date_created": j.get("date_created"), "date_created_ms": rfc2822_ms(j.get("date_created")),
        "date_sent": j.get("date_sent"), "date_sent_ms": rfc2822_ms(j.get("date_sent")),
        "date_updated": j.get("date_updated"), "num_segments": j.get("num_segments"),
    }


def send(nonce, to, *, ops=t, out_dir=DEFAULT_OUT, follow_s=60.0, poll_s=3.0, dry_run=False, wait_ready_s=120.0,
         now=time.time, sleep=time.sleep, say=print, alive=_pid_alive):
    body = body_for(nonce)
    to = check_to(to)
    if dry_run:
        try:
            line = line_number(ops.HOME)
        except Refused as exc:
            line = None
            say("DRY sender check: %s" % exc)
        say("DRY would POST %s/Messages.json From=%s To=%s Body=%r (one message, no retry)" % (
            "<account>", t.mask(line) if line else "[plugin line]", t.mask(to), body))
        say("DRY nothing read from the credential files, nothing sent, nothing written")
        return 0
    line = line_number(ops.HOME)
    os.makedirs(out_dir, exist_ok=True)
    now_ms = int(now() * 1000)
    busy = _recent_send(out_dir, now_ms)
    if busy:
        raise Refused("another probe was sent less than 15 minutes ago (%s)" % os.path.basename(busy))
    if wait_ready_s > 0:
        say("WAITING up to %.0f s for the reader (otp_delay.py --probe-nonce %s)" % (wait_ready_s, nonce))
        wait_ready(out_dir, nonce, wait_ready_s, now=now, sleep=sleep, alive=alive)
        say("READER is watching")
    s, b = ops.balance()
    if s != 200 or b[0] - BALANCE_MARGIN < ops.STOP_BALANCE:
        raise Refused("balance stop: %s" % (b,))
    path = record_path(out_dir, nonce)
    doc = {"tool": "tools/coordinator/send_latency_probe.py", "nonce": nonce, "body": body, "to": t.mask(to),
           "from": t.mask(line), "state": "reserved", "reserved_ms": now_ms, "reserved_utc": iso_ms(now_ms),
           "balance_before": b[0], "dry_run": False, "credentials_recorded": False}
    _reserve(path, doc)

    doc["request_ms"] = int(now() * 1000)
    try:
        st, j = ops.req("POST", ops.API + "/Messages.json", {"To": to, "From": line, "Body": body})
    except Exception as exc:   # network error: the message may or may not have gone out; never retry
        doc["state"] = "unknown_outcome"
        doc["error"] = type(exc).__name__
        doc["request_utc"] = iso_ms(doc["request_ms"])
        _write(path, doc)
        say("SEND OUTCOME UNKNOWN (%s): check the Twilio console before any new probe; no retry" % type(exc).__name__)
        return 6
    doc["response_ms"] = int(now() * 1000)
    doc["request_utc"], doc["response_utc"] = iso_ms(doc["request_ms"]), iso_ms(doc["response_ms"])
    doc["http"] = st
    j = j if isinstance(j, dict) else {}
    doc["twilio"] = _twilio_fields(j)
    sid = j.get("sid") if isinstance(j.get("sid"), str) and SID_RE.fullmatch(j.get("sid")) else None
    doc["message_sid"] = sid
    if st not in (200, 201) or sid is None:
        doc["state"] = "send_failed"
        _write(path, doc)
        say("SEND FAILED http=%s code=%s message=%s (no retry)" % (st, doc["twilio"]["error_code"], doc["twilio"]["error_message"]))
        return 6
    doc["state"] = "sent"
    _write(path, doc)
    say("SENT nonce=%s from=%s to=%s sid=%s request_utc=%s http=%s status=%s" % (
        nonce, doc["from"], doc["to"], sid, doc["request_utc"], st, doc["twilio"]["status"]))

    end = now() + max(0.0, follow_s)
    history = [{"t": doc["response_utc"], "status": doc["twilio"]["status"]}]
    while doc["twilio"]["status"] not in FINAL and now() < end:
        sleep(poll_s)
        try:
            gs, gj = ops.req("GET", ops.API + "/Messages/%s.json" % sid)
        except Exception:
            continue
        if gs != 200 or not isinstance(gj, dict):
            continue
        doc["twilio"] = _twilio_fields(gj)
        if doc["twilio"]["status"] != history[-1]["status"]:
            history.append({"t": iso_ms(int(now() * 1000)), "status": doc["twilio"]["status"]})
            say("STATUS %s at %s date_sent=%s" % (doc["twilio"]["status"], history[-1]["t"], doc["twilio"]["date_sent"]))
        doc["status_history"] = history
        _write(path, doc)
    doc["status_history"] = history
    doc["followed_s"] = follow_s
    _write(path, doc)
    say("RECORD %s status=%s error_code=%s" % (path, doc["twilio"]["status"], doc["twilio"]["error_code"]))
    return 6 if doc["twilio"]["status"] in ("undelivered", "failed", "canceled") else 0


def main(argv=None, **kw):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--new-nonce", action="store_true", help="print a fresh nonce and exit")
    ap.add_argument("--nonce")
    ap.add_argument("--to", default=None, help="must be the Fi line; anything else is refused")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--i-am-the-coordinator", action="store_true", help="required to send")
    ap.add_argument("--follow", type=float, default=60.0, help="seconds of read-only status GETs after the send")
    ap.add_argument("--wait-ready", type=float, default=120.0,
                    help="seconds to wait for otp_delay.py --probe-nonce to be watching (default 120; 0 = do not wait)")
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    say = kw.pop("say", print)
    if a.new_nonce:
        say(new_nonce())
        return 0
    if not a.nonce:
        ap.error("--nonce is required (get one with --new-nonce)")
    if not a.dry_run and not a.i_am_the_coordinator:
        ap.error("sending needs --i-am-the-coordinator (or use --dry-run)")
    if not 0 <= a.follow <= 300 or not 0 <= a.wait_ready <= 900:
        ap.error("--follow must be in [0, 300] and --wait-ready in [0, 900]")
    try:
        to = a.to if a.to is not None else initialize_numbers()
        return send(a.nonce, to, out_dir=a.out_dir, follow_s=a.follow, dry_run=a.dry_run,
                    wait_ready_s=a.wait_ready, say=say, **kw)
    except (Refused, ValueError) as exc:
        say("REFUSED %s" % exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
