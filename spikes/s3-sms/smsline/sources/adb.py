"""AdbSource: read the SMS inbox with `adb shell content query` (read-only).

What runs on the phone, and only this:
    getprop <name>                              device/OS facts for the probe record
    content query --uri content://sms/inbox ... read rows (SELECT only)
`content query` is a SELECT: no insert/update/delete/call/service verbs exist in this
module, and a test asserts the verb set.

Parsing is hardened because SMS text is attacker-controlled: a sender can put a line
that looks like `Row: 3 _id=...` inside a body. Rows must arrive with consecutive
indexes and, by default, the parsed `_id` list is checked against a second `_id`-only
query. A mismatch raises ParseError and nothing is released.
"""
from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

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
# body is last on purpose: every earlier field has a fixed, parseable shape.
PROJECTION = ("_id", "address", "date", "type", "body")
ALLOWED_REMOTE_VERBS = ("content query", "getprop")

_PROPS = (
    "ro.product.model",
    "ro.build.version.release",
    "ro.build.version.sdk",
    "ro.build.version.security_patch",
    "ro.build.version.release_or_codename",
)


class RunResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str], float], RunResult]


def default_runner(argv: Sequence[str], timeout: float) -> RunResult:
    try:
        p = subprocess.run(
            list(argv), capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, shell=False,
        )
    except subprocess.TimeoutExpired:
        raise DeviceUnavailable("adb timed out after %.0fs" % timeout)
    except FileNotFoundError:
        raise DeviceUnavailable("adb not found")
    return RunResult(p.returncode, p.stdout, p.stderr)


_ROW_START = re.compile(r"^Row: (\d+) _id=(\d+)(?:, |$)")
_ROW_FULL = re.compile(
    r"^Row: (\d+) _id=(\d+), address=(.*?), date=(-?\d+), type=(\d+), body=(.*)$", re.S
)
_ID_ONLY = re.compile(r"^Row: (\d+) _id=(\d+)$")


class _Row(NamedTuple):
    id: str
    address: str
    date_ms: int
    type: int
    body: str


def _norm(v: str) -> str:
    return "" if v == "NULL" else v


def parse_rows(stdout: str) -> List[_Row]:
    """Parse `content query --projection _id:address:date:type:body` output."""
    text = stdout.replace("\r\n", "\n")
    if text.endswith("\n"):
        text = text[:-1]
    if text.strip() in ("", "No result found."):
        return []
    chunks: List[List[str]] = []
    for line in text.split("\n"):
        m = _ROW_START.match(line)
        if m and int(m.group(1)) == len(chunks):
            chunks.append([line])
        elif chunks:
            chunks[-1].append(line)     # continuation of a multi-line body (or a forged row)
        else:
            raise ParseError("unexpected output before first row")
    rows: List[_Row] = []
    for i, chunk in enumerate(chunks):
        m = _ROW_FULL.match("\n".join(chunk))
        if not m:
            raise ParseError("row %d malformed" % i)
        rows.append(_Row(m.group(2), _norm(m.group(3)), int(m.group(4)), int(m.group(5)), _norm(m.group(6))))
    return rows


def parse_id_rows(stdout: str) -> List[str]:
    text = stdout.replace("\r\n", "\n").strip("\n")
    if text.strip() in ("", "No result found."):
        return []
    ids = []
    for i, line in enumerate(text.split("\n")):
        m = _ID_ONLY.match(line.strip())
        if not m or int(m.group(1)) != i:
            raise ParseError("id row %d malformed" % i)
        ids.append(m.group(2))
    return ids


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


def _classify_failure(out: str, err: str) -> Optional[SourceError]:
    blob = (out + "\n" + err)
    low = blob.lower()
    if "no devices/emulators found" in low or "no devices found" in low:
        return DeviceUnavailable("no device attached")
    if "unauthorized" in low:
        return DeviceUnavailable("device unauthorized: accept the USB debugging prompt on the phone")
    if "device offline" in low:
        return DeviceUnavailable("device offline")
    if "more than one device" in low:
        return DeviceUnavailable("more than one device: pass the serial")
    if "securityexception" in low or "permission denial" in low or "read_sms" in low:
        return PermissionDenied("shell was refused access to the SMS provider")
    if "unable to find provider" in low or "could not find provider" in low:
        return SourceError("SMS provider not found")
    if "exception" in low and "row:" not in low:
        return SourceError("content query failed")
    return None


class AdbSource(SmsSource):
    name = "adb"

    def __init__(
        self,
        serial: Optional[str] = None,
        adb_path: Optional[str] = None,
        runner: Runner = default_runner,
        user: Optional[int] = None,
        timeout_s: float = 15.0,
        verify_ids: bool = True,
        device_side_address_filter: bool = False,
    ) -> None:
        self.serial = serial
        self._adb = adb_path or shutil.which("adb") or "adb"
        self._run = runner
        self.user = user
        self.timeout_s = timeout_s
        self.verify_ids = verify_ids
        # Off by default: the provider stores the sender as received ("+1555...", "5550100",
        # "EXAMPLE"), so an exact-match SQL filter would miss spelling variants.
        self.device_side_address_filter = device_side_address_filter

    # ---- plumbing
    def _argv(self, *tail: str) -> List[str]:
        base = [self._adb]
        if self.serial:
            base += ["-s", self.serial]
        return base + list(tail)

    def _shell(self, remote: str) -> RunResult:
        if not remote.startswith(ALLOWED_REMOTE_VERBS):
            raise ValueError("remote command not on the read-only allow-list")
        res = self._run(self._argv("shell", remote), self.timeout_s)
        # stdout that starts like row data is data, never an error report: a message body
        # containing "exception" or "unauthorized" must not be mistaken for a failure.
        head = res.stdout.lstrip()
        is_data = head == "" or head.startswith("Row:") or head.startswith("No result found.")
        err = _classify_failure("" if is_data else res.stdout, res.stderr)
        if err is not None:
            raise err
        if res.returncode != 0:
            raise SourceError("adb exited with status %d" % res.returncode)
        return res

    def devices(self) -> List[Tuple[str, str]]:
        res = self._run([self._adb, "devices"], self.timeout_s)
        out = []
        for line in res.stdout.replace("\r", "").split("\n")[1:]:
            parts = line.split("\t")
            if len(parts) == 2 and parts[0].strip():
                out.append((parts[0].strip(), parts[1].strip()))
        return out

    def _require_device(self) -> Tuple[str, str]:
        devs = self.devices()
        if self.serial:
            devs = [d for d in devs if d[0] == self.serial]
        if not devs:
            raise DeviceUnavailable("no device attached")
        if len(devs) > 1:
            raise DeviceUnavailable("more than one device: pass the serial")
        serial, state = devs[0]
        if state == "unauthorized":
            raise DeviceUnavailable("device unauthorized: accept the USB debugging prompt on the phone")
        if state != "device":
            raise DeviceUnavailable("device state: %s" % state)
        return devs[0]

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
            serial, _ = self._require_device()
            self._query(("_id",), "_id<0", None)     # SELECT that matches nothing: tests access only
            info = self.device_info()
        except SourceError as exc:
            return SourceStatus(False, self.name, str(exc))
        info["serial_tail"] = serial[-4:]
        return SourceStatus(True, self.name, "READ_SMS query accepted", info)

    def fetch(self, *, since_ms=None, until_ms=None, addresses=None, limit=50) -> List[SmsMessage]:
        self._require_device()
        where = build_where(
            since_ms, until_ms, addresses if self.device_side_address_filter else None
        )
        rows = self._fetch_verified(where)
        msgs = [
            SmsMessage(id=r.id, address=r.address, date_ms=r.date_ms, body=r.body, source=self.name)
            for r in rows
            if r.type == 1
        ]
        return apply_filters(msgs, since_ms=since_ms, until_ms=until_ms, addresses=addresses, limit=limit)

    def _fetch_verified(self, where: str) -> List[_Row]:
        for attempt in (1, 2):
            res = self._query(PROJECTION, where, "date DESC")
            rows = parse_rows(res.stdout)
            if not self.verify_ids:
                return rows
            ids_res = self._query(("_id",), where, "date DESC")
            ids = parse_id_rows(ids_res.stdout)
            if [r.id for r in rows] == ids:
                return rows
            # A new message can land between the two queries; one retry covers that race.
            LOG.warning("adb id cross-check mismatch (attempt %d)", attempt)
        raise ParseError("row ids disagree with the id-only query; refusing to trust the output")
