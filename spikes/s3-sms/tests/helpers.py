"""Shared test helpers: a fake adb that behaves like the device, and time constants."""
from __future__ import annotations

import datetime as dt
import json
import os
import shlex
from typing import Dict, List, Optional, Sequence

from smsline.sources.adb import RunResult

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures")

# 2026-11-05 00:00:00 UTC, an arbitrary fixed instant
T0 = int(dt.datetime(2026, 11, 5, tzinfo=dt.timezone.utc).timestamp() * 1000)
MIN = 60_000


def load_otp_cases() -> List[dict]:
    with open(os.path.join(FIXTURES, "otp_cases.json"), encoding="utf-8") as fh:
        return json.load(fh)["cases"]


def load_messages() -> List[dict]:
    with open(os.path.join(FIXTURES, "messages.json"), encoding="utf-8") as fh:
        return json.load(fh)["messages"]


PIXEL_PROPS = {
    "ro.product.model": "Pixel 10 Pro XL",   # synthetic stand-in; the real probe fills this in
    "ro.build.version.release": "17",
    "ro.build.version.sdk": "37",
    "ro.build.version.security_patch": "2026-10-05",
    "ro.build.version.release_or_codename": "17",
}


class FakeAdb:
    """Stands in for the `adb` binary. Records every argv; rows come from `rows`.

    rows: list of dicts with _id, address, date, type, body (already in date DESC order
    unless the test wants otherwise).
    """

    def __init__(
        self,
        rows: Sequence[dict],
        props: Optional[Dict[str, str]] = None,
        state: Optional[str] = "device",
        deny: bool = False,
        serial: str = "FAKESERIAL1234",
    ) -> None:
        self.rows = list(rows)
        self.props = dict(props or PIXEL_PROPS)
        self.state = state
        self.deny = deny
        self.serial = serial
        self.calls: List[List[str]] = []
        self.stdout_override: Dict[str, str] = {}   # remote prefix -> raw stdout

    def __call__(self, argv: Sequence[str], timeout: float) -> RunResult:
        argv = list(argv)
        self.calls.append(argv)
        if argv[1:] == ["devices"]:
            lines = ["List of devices attached"]
            if self.state is not None:
                lines.append("%s\t%s" % (self.serial, self.state))
            return RunResult(0, "\n".join(lines) + "\n\n", "")
        # [adb, (-s serial)?, shell, remote]
        i = argv.index("shell")
        remote = argv[i + 1]
        toks = shlex.split(remote)
        if toks[0] == "getprop":
            return RunResult(0, self.props.get(toks[1], "") + "\n", "")
        assert toks[:2] == ["content", "query"], remote
        if self.deny:
            return RunResult(
                255, "",
                "java.lang.SecurityException: Permission Denial: opening provider "
                "com.android.providers.telephony.SmsProvider requires android.permission.READ_SMS\n",
            )
        for prefix, raw in self.stdout_override.items():
            if remote.startswith(prefix):
                return RunResult(0, raw, "")
        opts = {toks[k]: toks[k + 1] for k in range(2, len(toks) - 1, 2)}
        assert opts["--uri"] == "content://sms/inbox"
        where = opts.get("--where", "")
        rows = self._apply_where(where)
        proj = opts["--projection"].split(":")
        if not rows:
            return RunResult(0, "No result found.\n", "")
        out = []
        for n, r in enumerate(rows):
            out.append("Row: %d " % n + ", ".join("%s=%s" % (c, r[c]) for c in proj))
        return RunResult(0, "\n".join(out) + "\n", "")

    def _apply_where(self, where: str) -> List[dict]:
        rows = self.rows
        for part in [p for p in where.split(" AND ") if p]:
            if part.startswith("date>="):
                rows = [r for r in rows if int(r["date"]) >= int(part[6:])]
            elif part.startswith("date<="):
                rows = [r for r in rows if int(r["date"]) <= int(part[6:])]
            elif part == "_id<0":
                rows = []
            elif part.startswith("address IN"):
                allowed = {a.strip("'") for a in part[part.index("(") + 1 : part.rindex(")")].split(",")}
                rows = [r for r in rows if r["address"] in allowed]
            else:
                raise AssertionError("unexpected where: %r" % part)
        return rows

    def remote_commands(self) -> List[str]:
        return [c[c.index("shell") + 1] for c in self.calls if "shell" in c]


def fixture_messages(source: str = "fixture"):
    from smsline.model import SmsMessage

    return [
        SmsMessage(id=r["id"], address=r["address"], date_ms=T0 + r["minute"] * MIN, body=r["body"], source=source)
        for r in load_messages()
    ]


def adb_rows(messages):
    """Messages -> FakeAdb rows, newest first."""
    rows = [
        {"_id": m.id, "address": m.address, "date": m.date_ms, "type": 1, "body": m.body.reveal()}
        for m in messages
    ]
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows
