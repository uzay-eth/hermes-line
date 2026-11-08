#!/usr/bin/env python3
"""Live readiness probe: how long after an SMS is stamped does it become readable, and by us?

Owner step 4: Can asks a service to text a login code to the Fi line (Pixel on USB or wireless debugging).
Android 17 may hold OTP-looking messages back from non-default readers (S3 risk 1); this measures it.

    python3 tests/l4/live/otp_delay.py --minutes 15                       # from the repo root
    python3 tests/l4/live/otp_delay.py --minutes 3 --serial adb-<id>-<suffix>   # wireless debugging (mDNS name)

Send-and-measure (round 9): the coordinator texts "hermes-line latency probe <nonce>" from the plugin's Twilio line
to the Fi phone with `tools/coordinator/send_latency_probe.py`. Start this script first with the same nonce:

    python3 tests/l4/live/otp_delay.py --minutes 10 --serial adb-<id>-<suffix> --probe-nonce <nonce>
    python3 tests/l4/live/otp_delay.py --dry-run [--probe-nonce <nonce>]   # fake phone, fake send, virtual clock

With --probe-nonce the run writes `latency-probe-<nonce>-ready.json` next to its output once it has a baseline
(the send helper waits for it, so the probe is never sent before the reader is watching), finds the message that
carries the nonce, joins the helper's `latency-probe-<nonce>-send.json` and records: send time (Mac clock, just
before the Twilio request; Twilio's own date_sent), phone arrival (the SMS `date` column, phone clock; `date_sent`,
the network stamp), plugin read time (end of the first plugin fetch that returned it, Mac clock), and the derived
send->phone, phone->plugin and send->plugin seconds. The body is recorded only as `[TEXT] <nonce>`: it is matched
with `SecretText.fullmatch` and never unwrapped here. The run stops `--linger` seconds after the probe was read (or
as soon as the send record is final); exit 6 if the probe never showed up.

READ ONLY. It runs `adb shell getprop`, `adb shell content query` (SELECT), `adb shell date`, and for a
wireless phone `adb mdns services` / `adb connect` to recover a dropped link. It installs nothing, changes no
setting, sends nothing, dials nothing.

Every poll is one call of the plugin's own reader (`line.sms.AdbSource.fetch`, the path `release_otp` uses),
timed. For each NEW message (not in the inbox when the run started) it records the provider `date` and
`date_sent`, when the plugin's reader first returned it and how long that fetch took, the sender masked
(first character and last two), the body length, and whether a code-like token was present
(yes/no/ambiguous). One extra metadata query per new message (columns `_id`, `date`, `date_sent`: no `body`)
fetches `date_sent`. It NEVER prints or stores a message body or a code: the plugin fetch result only goes
through `extract_otp` for a yes/no and, with a probe nonce, through `fullmatch` for the nonce. Output: one
summary line per message on stdout and masked JSON in `evidence/sms/otp-delay-<utc>.json`.

Over Wi-Fi the link can drop (phone asleep, port change). A failed poll is logged as a `drop` event with its
time and reason, the run keeps polling, and the next good poll is logged as `recovered` with the down time;
the source's own reconnects (`adb mdns services` + `adb connect`, one per fetch) are logged too.

Lags are `first read by the plugin (Mac clock) - provider timestamp (phone clock)`. The phone clock offset is
estimated once from `adb shell date +%s` (resolution about one second) and reported as
`clock_skew_s_estimate`; `lag_s` values are raw and `lag_corrected_s` subtracts the estimate.

Exit codes: 0 ok, 3 waiting for an owner step (no device / unauthorized / unreachable at the start, or no
poll ever succeeded), 4 SMS access refused, 5 other error, 6 probe not seen (probe mode only).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import statistics
import sys
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, TextIO

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
if os.path.join(_ROOT, "plugin") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "plugin"))

from line.sms.masking import mask_sender  # noqa: E402
from line.sms.otp import extract_otp  # noqa: E402
from line.sms.sources import AdbSource, DeviceUnavailable, PermissionDenied, SourceError  # noqa: E402

META_PROJECTION = ("_id", "date", "date_sent")   # no `body`, no `address`: this query cannot return message text
_META_ROW = re.compile(r"^Row: (\d+) _id=(\d+), date=(-?\d+), date_sent=(-?\d+|NULL)$")
DEFAULT_OUT = os.path.join(_ROOT, "evidence", "sms")

PROBE_TEMPLATE = "hermes-line latency probe %s"      # the exact text tools/coordinator/send_latency_probe.py sends
NONCE_RE = re.compile(r"[a-z]{8,16}")
SEND_FINAL = ("delivered", "undelivered", "failed", "canceled")
DRY_NONCE = "dryrunprobe"


def utc_stamp(t: float) -> str:
    return dt.datetime.fromtimestamp(t, tz=dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def iso(t_ms: Optional[int]) -> Optional[str]:
    if t_ms is None or t_ms <= 0:
        return None
    return dt.datetime.fromtimestamp(t_ms / 1000.0, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_meta(stdout: str) -> List[Dict[str, Any]]:
    """Rows of the metadata query. Bodies are not selected, so every line has the fixed shape; anything else is refused."""
    text = stdout.replace("\r\n", "\n").strip("\n")
    if text.strip() in ("", "No result found."):
        return []
    rows = []
    for i, line in enumerate(text.split("\n")):
        m = _META_ROW.match(line.strip())
        if not m or int(m.group(1)) != i:
            raise SourceError("metadata row %d malformed" % i)
        rows.append({"id": m.group(2), "date": int(m.group(3)),
                     "date_sent": None if m.group(4) == "NULL" or int(m.group(4)) <= 0 else int(m.group(4))})
    return rows


def code_flag(msg: Any) -> str:
    res = extract_otp(msg.body)
    if res.ambiguous:
        return "ambiguous"
    return "yes" if res.primary is not None else "no"


def probe_view(body: Any, nonce: str) -> Optional[Dict[str, Any]]:
    """None unless the body carries `nonce` as a whole token; else the masked view `[TEXT] <nonce> [TEXT]`.

    Uses `SecretText.fullmatch` only (yes/no answers): the body is never unwrapped, so nothing but the nonce and
    whether text stands before/after it can reach the output.
    """
    tok = r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(nonce)
    if not body.fullmatch(r"(?s).*%s.*" % tok):
        return None
    before = body.fullmatch(r"(?s).+%s.*" % tok)
    after = body.fullmatch(r"(?s).*%s.+" % tok)
    masked = " ".join(p for p in ("[TEXT]" if before else "", nonce, "[TEXT]" if after else "") if p)
    return {"template_exact": body.fullmatch(re.escape(PROBE_TEMPLATE % nonce)), "body_masked": masked}


def _lag(a_ms: Optional[int], b_ms: Optional[int]) -> Optional[float]:
    return None if a_ms is None or b_ms is None else round((a_ms - b_ms) / 1000.0, 3)


def phone_clock_skew(source: AdbSource, now: Callable[[], float]) -> Optional[float]:
    """Estimated (phone clock - Mac clock) in seconds from `adb shell date +%s`; None when unavailable."""
    try:
        t0 = now()
        res = source._run(source._argv("shell", "date +%s"), source.timeout_s)
        t1 = now()
        phone = int(res.stdout.strip().splitlines()[-1])
    except Exception:
        return None
    return round(phone - (t0 + t1) / 2.0, 1)    # whole-second resolution: treat as +-1 s


# ---------------------------------------------------------------------- probe files (shared with the send helper)

def send_record_path(out_dir: str, nonce: str) -> str:
    return os.path.join(out_dir, "latency-probe-%s-send.json" % nonce)


def ready_path(out_dir: str, nonce: str) -> str:
    return os.path.join(out_dir, "latency-probe-%s-ready.json" % nonce)


def load_send_record(path: str, nonce: str) -> Optional[Dict[str, Any]]:
    """The helper's record, or None (missing, unreadable, or for another nonce)."""
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("nonce") == nonce else None


def send_final(sr: Optional[Dict[str, Any]]) -> bool:
    if sr is None:
        return False
    if sr.get("state") in ("send_failed", "unknown_outcome"):
        return True
    return sr.get("state") == "sent" and ("followed_s" in sr or (sr.get("twilio") or {}).get("status") in SEND_FINAL)


def send_failed(sr: Optional[Dict[str, Any]]) -> bool:
    if sr is None:
        return False
    return sr.get("state") in ("send_failed", "unknown_outcome") or (sr.get("twilio") or {}).get("status") in (
        "failed", "undelivered", "canceled")


def _num(v: Any) -> Optional[int]:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def probe_timings(probe: Dict[str, Any], sr: Optional[Dict[str, Any]], skew_s: Optional[float]) -> None:
    """Fill the send part and the derived seconds into `probe` (in place)."""
    tw = (sr or {}).get("twilio") or {}
    probe["send"] = None if sr is None else {
        "state": sr.get("state"), "dry_run": bool(sr.get("dry_run")), "request_utc": iso(_num(sr.get("request_ms"))),
        "response_utc": iso(_num(sr.get("response_ms"))), "message_sid": sr.get("message_sid"),
        "twilio_status": tw.get("status"), "twilio_error_code": tw.get("error_code"),
        "twilio_date_sent": tw.get("date_sent"), "status_history": sr.get("status_history"),
    }
    probe["clock_skew_s_estimate"] = skew_s
    req = _num((sr or {}).get("request_ms"))
    phone = _num(probe.get("phone_date_ms"))
    seen = _num(probe.get("first_seen_ms"))
    sk_ms = int(round((skew_s or 0.0) * 1000))
    phone_mac = None if phone is None else phone - sk_ms           # phone stamp on the Mac clock
    t_sent = _num(tw.get("date_sent_ms"))
    probe["send_to_phone_s"] = _lag(phone_mac, req)                 # skew-corrected (about +-1 s)
    probe["send_to_phone_raw_s"] = _lag(phone, req)
    probe["twilio_sent_to_phone_s"] = _lag(phone_mac, t_sent)       # Twilio stamps whole seconds
    probe["phone_to_plugin_s"] = _lag(seen, phone_mac)
    probe["send_to_plugin_s"] = _lag(seen, req)                     # Mac clock only: no skew involved
    prev = _num(probe.get("previous_poll_start_ms"))
    probe["read_resolution_s"] = _lag(seen, prev)                   # it became readable inside this window
    probe["started_before_send"] = None if req is None else bool(_num(probe.get("run_first_poll_ms")) is not None
                                                                   and probe["run_first_poll_ms"] <= req)
    for k in ("phone_date_ms", "first_seen_ms", "previous_poll_start_ms", "run_first_poll_ms"):
        probe.pop(k, None)


# ---------------------------------------------------------------------- the run

def run(
    source: AdbSource,
    minutes: float,
    interval_s: float = 2.0,
    lookback_min: float = 240.0,
    out_dir: str = DEFAULT_OUT,
    out: Optional[TextIO] = None,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    skew_fn: Optional[Callable[[], Optional[float]]] = None,
    probe_nonce: Optional[str] = None,
    send_record: Optional[str] = None,
    linger_s: float = 45.0,
    dry_run: bool = False,
) -> int:
    out = out or sys.stdout
    if probe_nonce is not None and not NONCE_RE.fullmatch(probe_nonce):
        raise ValueError("the probe nonce must be 8-16 lowercase letters")
    started = clock()
    events: List[Dict[str, Any]] = []
    doc: Dict[str, Any] = {
        "tool": "tests/l4/live/otp_delay.py", "started_utc": iso(int(started * 1000)), "minutes": minutes,
        "interval_s": interval_s, "lookback_min": lookback_min, "read_only": True, "status": "running",
        "bodies_or_codes_recorded": False, "messages": [], "events": events,
    }
    if dry_run:
        doc["dry_run"] = True
    probe: Optional[Dict[str, Any]] = None
    sr_path = None
    if probe_nonce is not None:
        sr_path = send_record or send_record_path(out_dir, probe_nonce)
        probe = doc["probe"] = {"nonce": probe_nonce, "template": PROBE_TEMPLATE % probe_nonce, "seen": False,
                                "send_record": os.path.basename(sr_path)}
    path = os.path.join(out_dir, "otp-delay-%s%s.json" % ("dry-" if dry_run else "", utc_stamp(started)))
    ready = ready_path(out_dir, probe_nonce) if probe_nonce is not None else None

    def save(status: Optional[str] = None) -> None:
        if status:
            doc["status"] = status
        doc["ended_utc"] = iso(int(clock() * 1000))
        os.makedirs(out_dir, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)

    def say(line: str) -> None:
        out.write(line + "\n")
        out.flush()

    def event(kind: str, **info: Any) -> None:
        rec = {"t": iso(int(clock() * 1000)), "event": kind}
        rec.update(info)
        events.append(rec)

    prev_hook = source.on_event

    def on_source_event(kind: str, info: Dict[str, Any]) -> None:
        event(kind, **{k: v for k, v in info.items() if k in ("reason", "address")})
        say("otp-delay: %s %s" % (kind, info.get("address", info.get("reason", ""))))
        if prev_hook is not None:
            prev_hook(kind, info)

    source.on_event = on_source_event

    def finish_messages() -> None:
        for r in doc["messages"]:
            r.pop("_date_ms", None)
        if probe is not None and "send" not in probe:
            probe_timings(probe, load_send_record(sr_path, probe_nonce), doc.get("clock_skew_s_estimate"))

    try:
        st = source.check()
        if not st.ok:
            waiting = any(k in st.detail for k in ("no device", "unauthorized", "offline", "state:", "more than one",
                                                   "unreachable", "not advertised"))
            doc["detail"] = st.detail
            save("waiting for owner step" if waiting else "failed")
            say("otp-delay: %s: %s (nothing was read)" % ("waiting for owner step" if waiting else "failed", st.detail))
            if "unauthorized" in st.detail:
                say("otp-delay: stop: unlock the Pixel and accept the debugging prompt for this Mac, then rerun")
            if probe is not None:
                say("otp-delay: no ready file written: the send helper will not send")
            say("otp-delay: wrote %s" % path)
            return 3 if waiting else (4 if "refused" in st.detail else 5)
        info = st.info
        sdk = info.get("ro.build.version.sdk", "")
        doc["device"] = {"model": info.get("ro.product.model", ""), "android": info.get("ro.build.version.release", ""),
                         "sdk": sdk, "security_patch": info.get("ro.build.version.security_patch", ""),
                         "serial_tail": info.get("serial_tail", ""), "transport": info.get("transport", "")}
        doc["android17_otp_protection_expected"] = bool(sdk.isdigit() and int(sdk) >= 37)
        skew = skew_fn() if skew_fn is not None else phone_clock_skew(source, clock)
        doc["clock_skew_s_estimate"] = skew
        say("otp-delay: device %s android=%s sdk=%s patch=%s transport=%s; android17 OTP protection expected: %s; "
            "phone clock offset ~%s s" % (doc["device"]["model"], doc["device"]["android"], sdk,
                                          doc["device"]["security_patch"], doc["device"]["transport"],
                                          doc["android17_otp_protection_expected"], skew))

        since_ms = int((started - lookback_min * 60) * 1000)
        by_id: Dict[str, Dict[str, Any]] = {}
        baseline: Optional[set] = None
        ok_ms: List[float] = []
        all_ms: List[float] = []
        polls = ok_polls = failed_polls = 0
        down_since: Optional[float] = None
        longest_down = 0.0
        prev_ok_start: Optional[float] = None
        seen_at: Optional[float] = None
        deadline = started + minutes * 60.0
        if probe is None:
            say("otp-delay: watching for %.1f min (poll every %.1f s). Ask the service to text the code now." % (minutes, interval_s))
        else:
            say("otp-delay: watching for %.1f min (poll every %.1f s) for probe nonce %s" % (minutes, interval_s, probe_nonce))
        while True:
            f0 = clock()
            try:
                msgs = source.fetch(since_ms=since_ms, limit=500)     # the plugin's own read path (release_otp uses it)
                err: Optional[str] = None
            except PermissionDenied:
                raise
            except SourceError as exc:                                # DeviceUnavailable, ParseError, ...: log, keep watching
                msgs, err = [], "%s: %s" % (type(exc).__name__, exc)
            f1 = clock()
            latency_ms = round((f1 - f0) * 1000.0, 1)
            polls += 1
            all_ms.append(latency_ms)
            if err is not None:
                failed_polls += 1
                if down_since is None:
                    down_since = f0
                    event("drop", detail=err[:160], fetch_ms=latency_ms)
                    say("otp-delay: DROP at %s: %s" % (iso(int(f0 * 1000)), err))
            else:
                ok_polls += 1
                ok_ms.append(latency_ms)
                if down_since is not None:
                    down = round(f1 - down_since, 1)
                    longest_down = max(longest_down, down)
                    event("recovered", down_s=down, fetch_ms=latency_ms)
                    say("otp-delay: RECOVERED at %s after %.1f s down" % (iso(int(f1 * 1000)), down))
                    down_since = None
                if baseline is None:
                    baseline = {m.id for m in msgs}
                    say("otp-delay: baseline %d message(s) already in the inbox; plugin read latency %.0f ms" % (len(baseline), latency_ms))
                    if probe is not None:
                        probe["run_first_poll_ms"] = int(f0 * 1000)
                        os.makedirs(out_dir, exist_ok=True)
                        with open(ready, "w", encoding="utf-8") as fh:
                            json.dump({"nonce": probe_nonce, "ready_ms": int(clock() * 1000), "pid": os.getpid()}, fh)
                        say("otp-delay: READY for probe %s: send it now" % probe_nonce)
                for m in msgs:
                    pv = probe_view(m.body, probe_nonce) if probe is not None and not probe["seen"] else None
                    is_new = m.id not in baseline and m.id not in by_id
                    if not is_new and pv is None:
                        continue
                    rec = by_id.get(m.id)
                    if rec is None:
                        skew_s = doc.get("clock_skew_s_estimate") or 0.0
                        rec = by_id[m.id] = {
                            "id": m.id, "from": mask_sender(m.address), "date": iso(m.date_ms), "date_sent": None,
                            "first_seen": iso(int(f1 * 1000)), "lag_s": _lag(int(f1 * 1000), m.date_ms),
                            "lag_corrected_s": round((f1 * 1000 - m.date_ms) / 1000.0 + skew_s, 3),
                            "fetch_ms": latency_ms, "code_like": code_flag(m), "body_len": len(m.body),
                            "date_sent_lag_s": None, "meta_query_ms": None, "_date_ms": m.date_ms,
                        }
                        if probe is not None:
                            rec["probe"] = pv is not None
                            rec["in_baseline"] = m.id in baseline
                        doc["messages"].append(rec)
                        t_m0 = clock()
                        try:
                            meta = parse_meta(source._query(META_PROJECTION, "_id=%d" % int(m.id), None).stdout)
                        except Exception:
                            meta = []
                        rec["meta_query_ms"] = round((clock() - t_m0) * 1000.0, 1)
                        if meta and meta[0]["id"] == m.id:
                            rec["date_sent"] = iso(meta[0]["date_sent"])
                            rec["date_sent_lag_s"] = _lag(int(f1 * 1000), meta[0]["date_sent"])
                        say("otp-delay: MSG id=%s from=%s code_like=%s len=%s received(date)=%s sent=%s lag=%ss (corrected %ss) "
                            "plugin_fetch=%.0fms" % (rec["id"], rec["from"], rec["code_like"], rec["body_len"], rec["date"],
                                                     rec["date_sent"], rec["lag_s"], rec["lag_corrected_s"], latency_ms))
                    if pv is not None:
                        seen_at = f1
                        probe.update({
                            "seen": True, "message_id": m.id, "from": rec["from"], "body_masked": pv["body_masked"],
                            "template_exact": pv["template_exact"], "body_len": rec["body_len"],
                            "code_like": rec["code_like"], "in_baseline": m.id in baseline,
                            "phone_date_utc": rec["date"], "phone_date_ms": m.date_ms, "phone_date_sent_utc": rec["date_sent"],
                            "first_seen_utc": iso(int(f1 * 1000)), "first_seen_ms": int(f1 * 1000),
                            "previous_poll_start_utc": iso(int(prev_ok_start * 1000)) if prev_ok_start else None,
                            "previous_poll_start_ms": int(prev_ok_start * 1000) if prev_ok_start else None,
                            "plugin_fetch_ms": latency_ms,
                        })
                        say("otp-delay: PROBE id=%s body=%s exact_template=%s phone_date=%s date_sent=%s plugin_read=%s fetch=%.0fms" % (
                            m.id, pv["body_masked"], pv["template_exact"], rec["date"], rec["date_sent"],
                            probe["first_seen_utc"], latency_ms))
                    save()
                prev_ok_start = f0
            if probe is not None:
                sr = load_send_record(sr_path, probe_nonce)
                if probe["seen"] and (send_final(sr) or clock() - seen_at >= linger_s):
                    break
                if not probe["seen"] and send_failed(sr):
                    say("otp-delay: the send record says the probe was not delivered (%s): stopping" % (
                        (sr.get("twilio") or {}).get("error_code") or sr.get("state")))
                    break
            if clock() >= deadline:
                break
            sleep(max(0.0, min(interval_s, deadline - clock())))
        if down_since is not None:
            longest_down = max(longest_down, round(clock() - down_since, 1))
            doc["ended_while_down"] = True
        doc["polls"] = polls
        doc["reachability"] = {
            "polls": polls, "ok_polls": ok_polls, "failed_polls": failed_polls,
            "drops": sum(1 for e in events if e["event"] == "drop"),
            "recoveries": sum(1 for e in events if e["event"] == "recovered"),
            "reconnect_attempts": source.stats["reconnect_attempts"], "reconnects_ok": source.stats["reconnects_ok"],
            "longest_down_s": longest_down,
        }
        if ok_ms:
            doc["plugin_read_latency_ms"] = {
                "n": len(ok_ms), "min": min(ok_ms), "median": round(statistics.median(ok_ms), 1),
                "p90": round(sorted(ok_ms)[min(len(ok_ms) - 1, int(len(ok_ms) * 0.9))], 1), "max": max(ok_ms)}
        doc["fetch_ms_all"] = all_ms[:2000]
        finish_messages()
        pending: List[str] = []
        doc["verdict_hint"] = _verdict(doc, pending)
        if ok_polls == 0:
            save("never reachable")
            say("otp-delay: no poll ever succeeded (%d failed): the phone was unreachable for the whole run" % failed_polls)
            say("otp-delay: wrote %s" % path)
            return 3
        r = doc["reachability"]
        pl = doc.get("plugin_read_latency_ms", {})
        say("otp-delay: done. polls=%d ok=%d failed=%d drops=%d reconnects=%d/%d new_messages=%d" % (
            polls, ok_polls, failed_polls, r["drops"], r["reconnects_ok"], r["reconnect_attempts"], len(doc["messages"])))
        say("otp-delay: plugin read latency ms min/median/p90/max = %s/%s/%s/%s" % (
            pl.get("min"), pl.get("median"), pl.get("p90"), pl.get("max")))
        if probe is not None:
            say("otp-delay: probe %s seen=%s send->phone=%ss phone->plugin=%ss send->plugin=%ss (read resolution %ss, "
                "twilio status %s)" % (probe_nonce, probe["seen"], probe.get("send_to_phone_s"), probe.get("phone_to_plugin_s"),
                                       probe.get("send_to_plugin_s"), probe.get("read_resolution_s"),
                                       (probe.get("send") or {}).get("twilio_status")))
        say("otp-delay: %s" % doc["verdict_hint"])
        if probe is not None and not probe["seen"]:
            save("probe not seen")
            say("otp-delay: wrote %s" % path)
            return 6
        save("ok")
        say("otp-delay: wrote %s" % path)
        return 0
    except DeviceUnavailable as exc:
        doc["detail"] = str(exc)
        finish_messages()
        save("waiting for owner step")
        say("otp-delay: waiting for owner step: %s (stopped)" % exc)
        return 3
    except PermissionDenied as exc:
        doc["detail"] = str(exc)
        finish_messages()
        save("failed")
        say("otp-delay: SMS access refused: %s" % exc)
        return 4
    except SourceError as exc:
        doc["detail"] = "%s: %s" % (type(exc).__name__, exc)
        finish_messages()
        save("failed")
        say("otp-delay: failed: %s" % doc["detail"])
        return 5
    except KeyboardInterrupt:
        finish_messages()
        save("interrupted")
        say("otp-delay: interrupted; partial results written to %s" % path)
        return 0
    finally:
        if ready is not None:
            try:
                os.remove(ready)
            except OSError:
                pass


def _verdict(doc: Dict[str, Any], pending: List[str]) -> str:
    msgs = doc["messages"]
    probe = doc.get("probe")
    if probe is not None:
        if not probe.get("seen"):
            sent = (probe.get("send") or {}).get("state")
            return "probe %s not seen during the run (send record: %s)" % (probe["nonce"], sent or "missing")
        return ("probe read: send->phone %ss, phone->plugin %ss, send->plugin %ss; a plain text (no code), so this is "
                "the baseline path, not the Android 17 OTP hold" % (probe.get("send_to_phone_s"),
                                                                    probe.get("phone_to_plugin_s"), probe.get("send_to_plugin_s")))
    if not msgs:
        return "no new message arrived during the run: nothing to conclude about the Android 17 delay"
    codes = [m for m in msgs if m.get("code_like") in ("yes", "ambiguous")]
    plain = [m for m in msgs if m.get("code_like") == "no"]
    r = doc.get("reachability", {})
    parts = ["%d new message(s), %d code-like, %d plain; %d drop(s), %d recover(ies)" % (
        len(msgs), len(codes), len(plain), r.get("drops", 0), r.get("recoveries", 0))]
    cl = [m["lag_s"] for m in codes if m.get("lag_s") is not None]
    pl = [m["lag_s"] for m in plain if m.get("lag_s") is not None]
    if cl:
        parts.append("code-like plugin lag s: %s" % ", ".join("%.1f" % x for x in cl))
    if pl:
        parts.append("plain plugin lag s: %s" % ", ".join("%.1f" % x for x in pl))
    if pending:
        parts.append("a message was in the provider but unreadable by the plugin or not yet shown: Android 17 OTP hold likely if it was code-like")
    elif cl and max(cl) > 60 and (not pl or max(pl) < 30):
        parts.append("code-like message delayed while plain ones were not: Android 17 OTP hold likely")
    elif cl and max(cl) <= 30:
        parts.append("no OTP delay seen: codes are readable within seconds")
    return "; ".join(parts)


# ---------------------------------------------------------------------- dry mode: fake phone, fake send

class _VirtualClock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += max(0.0, s)


class DryPhone:
    """A scripted Pixel behind the real AdbSource (the test suite's FakeAdb), plus a scripted coordinator send.

    Timeline from the first fetch after the reader is READY: the send record is written `send_after_s` later; the
    probe lands on the phone `transit_s` after that (phone clock `skew_s` ahead of the Mac); one poll drops first.
    """

    def __init__(self, clock: _VirtualClock, nonce: str, out_dir: str, *, skew_s: float = 1.0, fetch_s: float = 1.8,
                 send_after_s: float = 4.0, transit_s: float = 2.2, drop_once: bool = True) -> None:
        sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))   # tests/ (for l4.helpers)
        from l4.helpers import FakeAdb

        self.clock, self.nonce, self.out_dir = clock, nonce, out_dir
        self.skew_s, self.fetch_s, self.send_after_s, self.transit_s = skew_s, fetch_s, send_after_s, transit_s
        now_ms = int(clock() * 1000 + skew_s * 1000)
        self.fake = FakeAdb([
            {"_id": "41", "address": "+15550100123", "date": now_ms - 20 * 60000, "date_sent": now_ms - 20 * 60000 - 900,
             "type": 1, "body": "dry baseline message one"},
            {"_id": "40", "address": "ACME", "date": now_ms - 45 * 60000, "date_sent": now_ms - 45 * 60000 - 900,
             "type": 1, "body": "dry baseline message two"},
        ])
        self.drop_once = drop_once
        self.send_at: Optional[float] = None
        self.arrive_at: Optional[float] = None
        self.request_ms: Optional[int] = None
        self.delivered = False

    def __call__(self, argv, timeout):
        self.clock.t += self.fetch_s if any("content query" in a for a in argv) else 0.05
        now = self.clock()
        ready = os.path.exists(ready_path(self.out_dir, self.nonce))
        if ready and self.send_at is None:
            self.send_at = now + self.send_after_s
        if self.send_at is not None and self.request_ms is None and now >= self.send_at:
            self.request_ms = int(self.send_at * 1000)
            self.arrive_at = self.send_at + self.transit_s
            self._write_send("sent", "queued")
        if self.arrive_at is not None and now >= self.arrive_at and not self.delivered:
            phone_ms = int((self.arrive_at + self.skew_s) * 1000)
            self.fake.rows.insert(0, {"_id": "42", "address": "+17755550120", "date": phone_ms,
                                      "date_sent": int((self.send_at + 1.0) * 1000) // 1000 * 1000,
                                      "type": 1, "body": PROBE_TEMPLATE % self.nonce})
            self.delivered = True
            self._write_send("sent", "delivered", final=True)
        if argv[-1] == "date +%s":
            from line.sms.sources.adb import RunResult

            return RunResult(0, "%d\n" % int(now + self.skew_s), "")
        if self.drop_once and ready and any("content query" in a for a in argv) and "_id=" not in " ".join(argv):
            self.drop_once = False
            self.fake.state = "offline"
            try:
                return self.fake(argv, timeout)
            finally:
                self.fake.state = "device"
        return self.fake(argv, timeout)

    def _write_send(self, state: str, status: str, final: bool = False) -> None:
        sent_s = int(self.send_at + 1.0)
        doc = {"tool": "tests/l4/live/otp_delay.py --dry-run (simulated send)", "nonce": self.nonce, "dry_run": True,
               "state": state, "request_ms": self.request_ms, "response_ms": self.request_ms + 350,
               "message_sid": "SM" + "0" * 32, "to": "[owner phone (Fi)]", "from": "[the line]",
               "twilio": {"status": status, "error_code": None, "date_sent_ms": sent_s * 1000,
                          "date_sent": dt.datetime.fromtimestamp(sent_s, tz=dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")},
               "status_history": [{"status": "queued"}] + ([{"status": "delivered"}] if final else [])}
        if final:
            doc["followed_s"] = 0
        with open(send_record_path(self.out_dir, self.nonce), "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)


def dry_run(nonce: str, out_dir: str, out: Optional[TextIO] = None, minutes: float = 2.0, interval_s: float = 2.0,
            linger_s: float = 10.0) -> int:
    clock = _VirtualClock(time.time())
    phone = DryPhone(clock, nonce, out_dir)
    src = AdbSource(adb_path="adb", runner=phone, clock=clock)
    out = out or sys.stdout
    out.write("otp-delay: DRY RUN: fake phone and simulated send on a virtual clock; nothing is read from a device, "
              "nothing is sent; output in %s\n" % out_dir)
    return run(src, minutes, interval_s, 240.0, out_dir, out, clock=clock, sleep=clock.sleep, probe_nonce=nonce,
               linger_s=linger_s, dry_run=True)


def main(argv: Optional[List[str]] = None, source: Optional[AdbSource] = None, **kw: Any) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--minutes", type=float, default=15.0, help="how long to watch (default 15)")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between polls (default 2)")
    ap.add_argument("--lookback-minutes", type=float, default=240.0,
                    help="messages stamped within this window are read each poll; 240 catches a 3 h Android 17 hold being released")
    ap.add_argument("--serial", help="adb serial, or the mDNS service name / ip:port of a wireless-debugging phone")
    ap.add_argument("--out-dir", default=None, help="default evidence/sms (a temporary directory with --dry-run)")
    ap.add_argument("--probe-nonce", help="send-and-measure: the nonce of the coordinator's latency probe SMS")
    ap.add_argument("--send-record", help="the send helper's record (default <out-dir>/latency-probe-<nonce>-send.json)")
    ap.add_argument("--linger", type=float, default=45.0, help="seconds to keep polling after the probe was read (default 45)")
    ap.add_argument("--dry-run", action="store_true", help="fake phone and simulated send on a virtual clock")
    args = ap.parse_args(argv)
    if not 0 < args.minutes <= 24 * 60 or not 0.5 <= args.interval <= 60:
        ap.error("--minutes must be in (0, 1440] and --interval in [0.5, 60]")
    if args.probe_nonce is not None and not NONCE_RE.fullmatch(args.probe_nonce):
        ap.error("--probe-nonce must be 8-16 lowercase letters")
    if not 0 <= args.linger <= 600:
        ap.error("--linger must be in [0, 600]")
    if args.dry_run:
        out_dir = args.out_dir or tempfile.mkdtemp(prefix="otp-delay-dry-")
        return dry_run(args.probe_nonce or DRY_NONCE, out_dir, kw.get("out"), minutes=min(args.minutes, 5.0),
                       interval_s=args.interval)
    src = source or AdbSource(serial=args.serial)
    return run(src, args.minutes, args.interval, args.lookback_minutes, args.out_dir or DEFAULT_OUT,
               probe_nonce=args.probe_nonce, send_record=args.send_record, linger_s=args.linger, **kw)


if __name__ == "__main__":
    sys.exit(main())
