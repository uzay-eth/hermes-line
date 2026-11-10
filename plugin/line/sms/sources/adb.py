"""AdbSource: read the SMS inbox with `adb shell content query` (read-only), over USB or Wi-Fi.

What runs on the phone, and only this:
    getprop <name>                              device/OS facts for the probe record
    content query --uri content://sms/inbox ... read rows (SELECT only)
`content query` is a SELECT: no insert/update/delete/call/service verbs exist in this
module, and a test asserts the verb set.

One `adb shell` per fetch (a `content query` costs 2-6 s of phone-side start-up over Wi-Fi, so a second
query for verification would double the latency). The row format is length-prefixed instead:
the projection asks the provider for `length(address)` and `length(body)` and the parser takes exactly that
many characters for each free-text field. SMS text is attacker-controlled (a body can contain a line that
looks like `Row: 3 _id=...`), but a forged row can never satisfy the lengths the provider reported for the
real row, so it is refused (ParseError) and nothing is released. This replaces the earlier second `_id`-only
query with a stronger check (it also covers the address field).

Targets (`serial`): a USB serial, an mDNS service name of a wireless-debugging phone
(`adb-<id>-<suffix>` with or without `._adb-tls-connect._tcp`), or `ip:port`. A configured serial is always
passed as `-s`: the source never silently uses "the only device". When a network target is not found, offline or
times out, the source does ONE bounded recovery per fetch (<= `reconnect_budget_s`, default 10 s):
`adb mdns services`, match the service name, `adb connect <ip:port>`, retry the query once.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import time
from typing import Any, Callable, Dict, List, Mapping, NamedTuple, Optional, Sequence, Tuple

from ..log import LOG
from ..model import SmsMessage, is_safe_address
from .base import (
    DeviceUnavailable,
    ParseError,
    PermissionDenied,
    SmsSource,
    SourceError,
    SourceStatus,
    apply_filters,
)

INBOX_URI = "content://sms/inbox"
# Length-prefixed: the two free-text fields come last and are preceded by their exact character lengths.
PROJECTION = ("_id", "date", "type", "length(address)", "length(body)", "address", "body")
ALLOWED_REMOTE_VERBS = ("content query", "getprop")

_PROPS = (
    "ro.product.model",
    "ro.build.version.release",
    "ro.build.version.sdk",
    "ro.build.version.security_patch",
    "ro.build.version.release_or_codename",
)


MDNS_SUFFIX = "._adb-tls-connect._tcp"
_MDNS_BASE = re.compile(r"^adb-[A-Za-z0-9]+-[A-Za-z0-9]+$")
_HOSTPORT = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})$")

MSG_UNREACHABLE = "phone unreachable over Wi-Fi (asleep or off the network)"
MSG_NOT_ADVERTISED = "paired device not advertised on the network (wireless debugging is off, or the phone is asleep or on another Wi-Fi)"
MSG_UNAUTHORIZED_USB = "device unauthorized: accept the USB debugging prompt on the phone"
MSG_UNAUTHORIZED_WIFI = "device unauthorized: accept the wireless debugging prompt on the phone, or pair again with `adb pair`"


class RunResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str], float], RunResult]

# adb gets only what it needs from the environment, never the gateway's whole one (provider keys, secrets): the adb
# server that a first call may start keeps its environment for its whole life (Nous review of d92bc1fb).
ADB_ENV_NAMES = ("PATH", "HOME", "LANG")
ADB_ENV_PREFIXES = ("ANDROID_", "ADB_")


def adb_env(environ: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    source = os.environ if environ is None else environ
    return {k: v for k, v in source.items() if k in ADB_ENV_NAMES or k.startswith(ADB_ENV_PREFIXES)}


def default_runner(argv: Sequence[str], timeout: float) -> RunResult:
    try:
        p = subprocess.run(
            list(argv), capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, shell=False, env=adb_env(),
        )
    except subprocess.TimeoutExpired:
        raise DeviceUnavailable("adb timed out after %.0fs" % timeout)
    except FileNotFoundError:
        raise DeviceUnavailable("adb not found")
    return RunResult(p.returncode, p.stdout, p.stderr)


_HEADER = re.compile(
    r"Row: (\d+) _id=(\d+), date=(-?\d+), type=(\d+), length\(address\)=(\d+|NULL), length\(body\)=(\d+|NULL), address="
)


class _Row(NamedTuple):
    id: str
    address: str
    date_ms: int
    type: int
    body: str


def _take(text: str, pos: int, length: Optional[int]) -> Tuple[str, int]:
    """Exactly `length` characters from `pos`; a NULL column prints as the four characters NULL."""
    if length is None:
        if not text.startswith("NULL", pos):
            raise ParseError("row field malformed")
        return "", pos + 4
    end = pos + length
    if end > len(text):
        raise ParseError("row field shorter than the provider's reported length")
    return text[pos:end], end


def _parse_exact(text: str) -> List[_Row]:
    rows: List[_Row] = []
    pos = 0
    while pos < len(text):
        m = _HEADER.match(text, pos)
        if not m or int(m.group(1)) != len(rows):
            raise ParseError("row %d malformed" % len(rows))
        la = None if m.group(5) == "NULL" else int(m.group(5))
        lb = None if m.group(6) == "NULL" else int(m.group(6))
        address, p = _take(text, m.end(), la)
        if not text.startswith(", body=", p):
            raise ParseError("row %d malformed" % len(rows))
        body, p = _take(text, p + len(", body="), lb)
        rows.append(_Row(m.group(2), address, int(m.group(3)), int(m.group(4)), body))
        if p == len(text):
            break
        if text[p] != "\n":
            raise ParseError("row %d: text after the reported length" % (len(rows) - 1))
        pos = p + 1
    return rows


def parse_rows(stdout: str) -> List[_Row]:
    """Parse `content query` output for PROJECTION. Anything that does not match the reported lengths is refused."""
    if stdout.strip() in ("", "No result found."):
        return []
    try:
        return _parse_exact(stdout)
    except ParseError:
        if "\r\n" not in stdout:
            raise
        return _parse_exact(stdout.replace("\r\n", "\n"))   # an adb that translates line endings


def build_where(since_ms: Optional[int], until_ms: Optional[int], addresses: Optional[Sequence[str]] = None) -> str:
    parts: List[str] = []
    if since_ms is not None:
        parts.append("date>=%d" % int(since_ms))
    if until_ms is not None:
        parts.append("date<=%d" % int(until_ms))
    if addresses:
        for a in addresses:
            if not is_safe_address(a) or "'" in a:
                raise ValueError("address not allowed in an on-device filter")
        parts.append("address IN (%s)" % ",".join("'%s'" % a for a in addresses))
    return " AND ".join(parts)


class _Gone(DeviceUnavailable):
    """The transport is missing, offline, closed or timed out: for a network target this is worth one reconnect."""


def _classify_failure(out: str, err: str) -> Optional[SourceError]:
    blob = (out + "\n" + err)
    low = blob.lower()
    if "no devices/emulators found" in low or "no devices found" in low:
        return _Gone("no device attached")
    if "unauthorized" in low:
        return DeviceUnavailable(MSG_UNAUTHORIZED_USB)
    if "device offline" in low:
        return _Gone("device offline")
    if "more than one device" in low:
        return DeviceUnavailable("more than one device: pass the serial")
    if re.search(r"device '[^']*' not found|device not found", low):
        return _Gone("device not found")
    if "error: closed" in low or "connection reset" in low or "protocol fault" in low or "connection refused" in low:
        return _Gone("device connection dropped")
    if "securityexception" in low or "permission denial" in low or "read_sms" in low:
        return PermissionDenied("shell was refused access to the SMS provider")
    if "unable to find provider" in low or "could not find provider" in low:
        return SourceError("SMS provider not found")
    if "exception" in low and "row:" not in low:
        return SourceError("content query failed")
    return None


def classify_serial(serial: Optional[str]) -> Tuple[str, Optional[str]]:
    """('usb' | 'mdns' | 'hostport' | 'none', normalised serial). mDNS names get the full service suffix adb needs."""
    if not serial:
        return "none", None
    s = serial.strip()
    base = s[: -len(MDNS_SUFFIX)] if s.endswith(MDNS_SUFFIX) else s
    if _MDNS_BASE.match(base):
        return "mdns", base + MDNS_SUFFIX
    if _HOSTPORT.match(s):
        return "hostport", s
    return "usb", s


def parse_mdns_services(stdout: str) -> List[Tuple[str, str, str]]:
    """`adb mdns services` lines -> (name, type, ip:port)."""
    out = []
    for line in stdout.replace("\r", "").split("\n"):
        parts = line.split("\t")
        if len(parts) == 3 and _HOSTPORT.match(parts[2].strip()):
            out.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))
    return out


class AdbSource(SmsSource):
    name = "adb"

    def __init__(
        self,
        serial: Optional[str] = None,
        adb_path: Optional[str] = None,
        runner: Runner = default_runner,
        user: Optional[int] = None,
        timeout_s: Optional[float] = None,
        device_side_address_filter: bool = False,
        reconnect_budget_s: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
        on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> None:
        self.kind, self.serial = classify_serial(serial)
        self._adb = adb_path or shutil.which("adb") or "adb"
        self._run = runner
        self.user = user
        # A `content query` takes 3-6 s (up to ~14 s) of phone-side start-up over Wi-Fi: a 15 s timeout would
        # mistake a slow read for a dead link and reconnect. USB keeps 15 s.
        self.timeout_s = timeout_s if timeout_s is not None else (30.0 if self.kind in ("mdns", "hostport") else 15.0)
        self.reconnect_budget_s = reconnect_budget_s
        self._clock = clock
        self.on_event = on_event
        self._route: Optional[str] = None      # ip:port learned by a reconnect; tried first until it fails
        self.stats = {"reconnect_attempts": 0, "reconnects_ok": 0}
        # Off by default: the provider stores the sender as received ("+1555...", "5550100", "EXAMPLE"),
        # so an exact-match SQL filter would miss spelling variants.
        self.device_side_address_filter = device_side_address_filter

    # ---- plumbing
    def _argv(self, *tail: str, route: Optional[str] = None) -> List[str]:
        target = route if route is not None else (self._route or self.serial)
        base = [self._adb]
        if target:
            base += ["-s", target]
        return base + list(tail)

    def _emit(self, kind: str, **info: Any) -> None:
        if self.on_event is not None:
            try:
                self.on_event(kind, info)
            except Exception:   # an observer must never change the outcome
                pass

    def _once(self, remote: str, route: Optional[str]) -> RunResult:
        """One `adb shell`; raises the classified error (a _Gone for transport loss)."""
        res = self._run(self._argv("shell", remote, route=route), self.timeout_s)
        # stdout that starts like row data is data, never an error report: a message body
        # containing "exception" or "unauthorized" must not be mistaken for a failure.
        head = res.stdout.lstrip()
        is_data = head == "" or head.startswith("Row:") or head.startswith("No result found.")
        err = _classify_failure("" if is_data else res.stdout, res.stderr)
        if err is not None:
            raise err
        if res.returncode != 0:
            raise _Gone("adb exited with status %d" % res.returncode)
        return res

    def _shell(self, remote: str) -> RunResult:
        if not remote.startswith(ALLOWED_REMOTE_VERBS):
            raise ValueError("remote command not on the read-only allow-list")
        route = self._route or self.serial
        try:
            return self._once(remote, route)
        except DeviceUnavailable as exc:
            gone = isinstance(exc, _Gone) or "timed out" in str(exc)
            if not gone or self.kind not in ("mdns", "hostport"):
                raise self._plain(exc)
            first = exc
        # network target lost: ONE bounded reconnect, then retry the query once
        self._route = None
        addr = self._reconnect(str(first))
        try:
            res = self._once(remote, addr)
        except DeviceUnavailable as exc:
            if isinstance(exc, _Gone) or "timed out" in str(exc):
                raise DeviceUnavailable(MSG_UNREACHABLE)
            raise self._plain(exc)
        self._route = addr
        return res

    def _plain(self, exc: DeviceUnavailable) -> DeviceUnavailable:
        """Turn the internal transport-loss marker into a plain DeviceUnavailable with the right wording."""
        msg = str(exc)
        if msg == MSG_UNAUTHORIZED_USB and self.kind in ("mdns", "hostport"):
            msg = MSG_UNAUTHORIZED_WIFI
        if self.kind in ("mdns", "hostport") and isinstance(exc, _Gone):
            msg = MSG_UNREACHABLE
        return DeviceUnavailable(msg)

    def _reconnect(self, reason: str) -> str:
        start = self._clock()

        def left() -> float:
            return self.reconnect_budget_s - (self._clock() - start)

        self.stats["reconnect_attempts"] += 1
        self._emit("reconnect_attempt", reason=reason[:80])
        try:
            if self.kind == "mdns":
                if left() <= 0:
                    raise DeviceUnavailable(MSG_UNREACHABLE)
                res = self._run([self._adb, "mdns", "services"], max(1.0, min(5.0, left())))
                base = self.serial[: -len(MDNS_SUFFIX)] if self.serial.endswith(MDNS_SUFFIX) else self.serial
                hits = [a for n, t, a in parse_mdns_services(res.stdout) if n == base and t == MDNS_SUFFIX[1:]]
                if not hits:
                    raise DeviceUnavailable(MSG_NOT_ADVERTISED)
                addr = hits[0]
            else:
                addr = self.serial
            if left() <= 0:
                raise DeviceUnavailable(MSG_UNREACHABLE)
            res = self._run([self._adb, "connect", addr], max(1.0, min(8.0, left())))
        except DeviceUnavailable as exc:
            if str(exc) in (MSG_NOT_ADVERTISED, MSG_UNREACHABLE):
                raise
            raise DeviceUnavailable(MSG_UNREACHABLE)
        out = (res.stdout + "\n" + res.stderr).lower()
        if "unauthorized" in out or "authenticate" in out:
            raise DeviceUnavailable(MSG_UNAUTHORIZED_WIFI)
        if "connected to" not in out or "failed" in out or "cannot" in out:
            raise DeviceUnavailable(MSG_UNREACHABLE)
        self.stats["reconnects_ok"] += 1
        self._emit("reconnected", address=addr)
        return addr

    def devices(self) -> List[Tuple[str, str]]:
        res = self._run([self._adb, "devices"], self.timeout_s)
        out = []
        for line in res.stdout.replace("\r", "").split("\n")[1:]:
            parts = line.split("\t")
            if len(parts) == 2 and parts[0].strip():
                out.append((parts[0].strip(), parts[1].strip()))
        return out

    def device_info(self) -> Dict[str, str]:
        info: Dict[str, str] = {}
        for prop in _PROPS:
            res = self._shell("getprop " + shlex.quote(prop))
            info[prop] = res.stdout.strip()
        return info

    def _query(self, projection: Sequence[str], where: str, sort: Optional[str]) -> RunResult:
        tokens = ["content", "query", "--uri", INBOX_URI]
        if self.user is not None:
            tokens += ["--user", str(int(self.user))]
        tokens += ["--projection", ":".join(projection)]
        if where:
            tokens += ["--where", where]
        if sort:
            tokens += ["--sort", sort]
        return self._shell(" ".join(shlex.quote(t) for t in tokens))

    # ---- SmsSource
    def check(self) -> SourceStatus:
        try:
            self._query(("_id",), "_id<0", None)     # SELECT that matches nothing: tests access only
            info = self.device_info()
            if self.serial:
                tail = (self.serial[: -len(MDNS_SUFFIX)] if self.serial.endswith(MDNS_SUFFIX) else self.serial)[-4:]
            else:
                tail = self._shell_free(["get-serialno"]).strip()[-4:]
        except SourceError as exc:
            return SourceStatus(False, self.name, str(exc))
        info["serial_tail"] = tail
        info["transport"] = {"usb": "usb", "mdns": "wifi", "hostport": "wifi"}.get(self.kind, "usb")
        return SourceStatus(True, self.name, "READ_SMS query accepted", info)

    def _shell_free(self, tail: List[str]) -> str:
        """An adb host command with no device shell (get-serialno); output is a serial, never SMS text."""
        res = self._run(self._argv(*tail), self.timeout_s)
        return res.stdout if res.returncode == 0 else ""

    def fetch(self, *, since_ms=None, until_ms=None, addresses=None, limit=50) -> List[SmsMessage]:
        where = build_where(
            since_ms, until_ms, addresses if self.device_side_address_filter else None
        )
        res = self._query(PROJECTION, where, "date DESC")
        rows = parse_rows(res.stdout)
        msgs = [
            SmsMessage(id=r.id, address=r.address, date_ms=r.date_ms, body=r.body, source=self.name)
            for r in rows
            if r.type == 1
        ]
        return apply_filters(msgs, since_ms=since_ms, until_ms=until_ms, addresses=addresses, limit=limit)
