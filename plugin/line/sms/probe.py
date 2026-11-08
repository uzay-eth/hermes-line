"""Read-only SMS probe for the Pixel on USB. Prints JSON lines; never prints a body.

    python3 -m line.sms.probe check                       # device, Android version, READ_SMS access
    python3 -m line.sms.probe inbox --since-minutes 60    # masked listing of recent messages
    python3 -m line.sms.probe watch --duration 600        # how long until a new message is visible

What it does on the phone: `getprop` reads and `content query` SELECTs. Nothing is
installed, changed, sent or dialled (line/sms/sources/adb.py has an allow-list and a test
for it). What it prints per message: id, masked sender, UTC time, body length, the
`redact_all` shape of the text (letters -> a/A, digits -> 9, ~40 chars) and whether an
OTP was detected (count, kinds). Message text and codes are never printed or stored.

Exit codes: 0 ok, 2 usage, 3 waiting for owner step (no/unauthorised device),
4 SMS access refused, 5 unexpected output / other source error.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Callable, Dict, List, Optional, TextIO

from .masking import mask_sender, redact_all
from .model import SmsMessage, iso_utc
from .otp import extract_otp
from .sources import AdbSource, DeviceUnavailable, ParseError, PermissionDenied, SmsSource, SourceError

SHAPE_CHARS = 40


def describe(m: SmsMessage) -> Dict[str, object]:
    """Masked record of one message. The only function that turns a message into output."""
    res = extract_otp(m.body)
    return {
        "id": m.id,
        "from": mask_sender(m.address),
        "at": iso_utc(m.date_ms),
        "len": len(m.body),
        "shape": redact_all(m.body.reveal(), max_len=SHAPE_CHARS),
        "otp": {
            "found": res.primary is not None or res.ambiguous,
            "codes": len(res.candidates),
            "ambiguous": res.ambiguous,
            "kinds": sorted({c.kind for c in res.candidates}),
        },
    }


def _emit(out: TextIO, obj: Dict[str, object]) -> None:
    out.write(json.dumps(obj, ensure_ascii=True, sort_keys=True) + "\n")
    out.flush()


def _waiting(out: TextIO, detail: str) -> int:
    _emit(out, {"probe": "status", "status": "waiting for owner step", "detail": detail,
                "owner_steps": ["plug the Pixel into the Mac by USB",
                                "unlock it and accept 'Allow USB debugging?' for this Mac (tick 'Always allow')"]})
    return 3


def cmd_check(src: SmsSource, args, out: TextIO) -> int:
    st = src.check()
    if not st.ok:
        if any(k in st.detail for k in ("no device", "unauthorized", "offline", "state:", "more than one", "unreachable", "not advertised")):
            return _waiting(out, st.detail)
        _emit(out, {"probe": "check", "status": "failed", "detail": st.detail})
        return 4 if "refused" in st.detail else 5
    info = st.info
    sdk = info.get("ro.build.version.sdk", "")
    _emit(out, {
        "probe": "check", "status": "ok", "detail": st.detail,
        "device": {
            "model": info.get("ro.product.model", ""),
            "android": info.get("ro.build.version.release", ""),
            "sdk": sdk,
            "security_patch": info.get("ro.build.version.security_patch", ""),
            "serial_tail": info.get("serial_tail", ""),
        },
        # Android 17 = API 37: SMS OTP protection (3-hour delay) may hide fresh codes from non-default readers
        "android17_otp_protection_expected": bool(sdk.isdigit() and int(sdk) >= 37),
    })
    return 0


def cmd_inbox(src: SmsSource, args, out: TextIO, clock: Callable[[], float]) -> int:
    since = int((clock() - args.since_minutes * 60) * 1000)
    msgs = src.fetch(since_ms=since, limit=args.limit)
    _emit(out, {"probe": "inbox", "status": "ok", "since_minutes": args.since_minutes, "count": len(msgs)})
    for m in msgs:
        _emit(out, dict(describe(m), probe="message"))
    return 0


def cmd_watch(src: SmsSource, args, out: TextIO, clock: Callable[[], float], sleep: Callable[[float], None]) -> int:
    """Poll and report, per NEW message, lag = first time we could see it - time the phone stamped.

    OTP-shaped messages that show up hours late while plain ones show up in seconds are
    the signature of Android 17's SMS OTP protection applying to the shell reader.
    """
    start = clock()
    seen: Dict[str, float] = {}
    baseline = {m.id for m in src.fetch(since_ms=int((start - 3600) * 1000), limit=500)}
    seen.update({i: start for i in baseline})
    lags: List[Dict[str, object]] = []
    _emit(out, {"probe": "watch", "status": "started", "baseline_messages": len(baseline), "duration_s": args.duration})
    while clock() - start < args.duration:
        sleep(args.interval)
        now = clock()
        for m in src.fetch(since_ms=int((start - 3600) * 1000), limit=500):
            if m.id in seen:
                continue
            seen[m.id] = now
            rec = dict(describe(m), probe="new_message", lag_s=round(now - m.date_ms / 1000.0, 1))
            lags.append(rec)
            _emit(out, rec)
    otp_lags = [r["lag_s"] for r in lags if r["otp"]["found"]]      # type: ignore[index]
    plain_lags = [r["lag_s"] for r in lags if not r["otp"]["found"]]  # type: ignore[index]
    _emit(out, {"probe": "watch", "status": "done", "new_messages": len(lags),
                "otp_shaped_lag_s": otp_lags, "plain_lag_s": plain_lags})
    return 0


def main(argv=None, source: Optional[SmsSource] = None, out: TextIO = None,
         clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep) -> int:
    out = out or sys.stdout
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serial", help="adb serial when more than one device is attached")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    p_in = sub.add_parser("inbox")
    p_in.add_argument("--since-minutes", type=int, default=60)
    p_in.add_argument("--limit", type=int, default=20)
    p_w = sub.add_parser("watch")
    p_w.add_argument("--duration", type=float, default=600.0)
    p_w.add_argument("--interval", type=float, default=5.0)
    args = ap.parse_args(argv)

    src = source or AdbSource(serial=args.serial)
    try:
        if args.cmd == "check":
            return cmd_check(src, args, out)
        if args.cmd == "inbox":
            return cmd_inbox(src, args, out, clock)
        return cmd_watch(src, args, out, clock, sleep)
    except DeviceUnavailable as exc:
        return _waiting(out, str(exc))
    except PermissionDenied as exc:
        _emit(out, {"probe": args.cmd, "status": "failed", "detail": str(exc)})
        return 4
    except (ParseError, SourceError) as exc:
        _emit(out, {"probe": args.cmd, "status": "failed", "detail": "%s: %s" % (type(exc).__name__, exc)})
        return 5


if __name__ == "__main__":
    sys.exit(main())
