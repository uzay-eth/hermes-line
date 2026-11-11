"""No-cost setup check: ``/line check`` in a Hermes chat, ``hermes hermes-line check`` in a shell.

It places no call and sends no text. Twilio is asked only with read-only GET requests (the account's number
list, filtered to the configured numbers, and its verified caller IDs); nothing is created, changed or bought.
Every item prints one plain line, ``OK`` or ``FIX`` with what to do. Credentials and SIDs are never printed;
a phone number is shown only by its last two digits. ``exit_code`` is 0 only when every item passes.

Each probe is injected (``Probes``), so the tests run with fakes and no network.
"""
from __future__ import annotations

import base64
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

MIN_HERMES = (0, 22, 5)
TWILIO_API = "https://api.twilio.com/2010-04-01"
TIMEOUT_S = 10.0
REQUIRED_SECRETS = ("HERMES_LINE_TWILIO_ACCOUNT_SID", "HERMES_LINE_TWILIO_API_KEY_SID",
                    "HERMES_LINE_TWILIO_API_KEY_SECRET")
_ACCOUNT = re.compile(r"AC[0-9a-f]{32}")
_KEY = re.compile(r"SK[0-9a-f]{32}")


@dataclass
class Item:
    name: str
    ok: bool
    text: str

    def line(self) -> str:
        return f"{'OK ' if self.ok else 'FIX'}  {self.name}: {self.text}"


@dataclass
class Probes:
    """What the check reads, each a plain function (the plugin's loaders in Hermes, fakes in tests)."""
    hermes_version: Callable[[], str]                  # "0.23.0"
    gateway_running: Callable[[], bool]
    get_secret: Callable[[str], Optional[str]]         # None when unset; raises when it cannot be read
    http_get: Callable[[str, dict, float], tuple]      # (url, headers, timeout) -> (status, body bytes); OSError
    port_open: Callable[[str, int, float], bool]
    profile_problems: Callable[[str], list]            # the call-profile check the plugin runs before each call
    destination_allowed: Callable[[str, dict], bool]   # (e164, policy settings) -> on the dial allowlist


def ending(e164: str) -> str:
    """A number as the check prints it: its last two digits only."""
    digits = re.sub(r"\D", "", e164 or "")
    return f"number ending in {digits[-2:]}" if len(digits) >= 2 else "number"


def _version(text: str) -> Optional[tuple]:
    m = re.match(r"\s*v?(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    return tuple(int(g or 0) for g in m.groups()) if m else None


class _Twilio:
    """Read-only Twilio REST: GET only, account-scoped list resources."""

    def __init__(self, probes: Probes, account: str, key: str, secret: str):
        self._get = probes.http_get
        self._base = f"{TWILIO_API}/Accounts/{account}"
        token = base64.b64encode(f"{key}:{secret}".encode()).decode()
        self._headers = {"Authorization": f"Basic {token}", "Accept": "application/json"}

    def list(self, resource: str, **query: str) -> tuple:
        """(HTTP status, parsed JSON or {}). Raises OSError when Twilio cannot be reached."""
        url = f"{self._base}/{resource}.json?{urllib.parse.urlencode(query)}"
        status, body = self._get(url, dict(self._headers), TIMEOUT_S)
        try:
            data = json.loads((body or b"{}").decode("utf-8", "replace") or "{}")
        except ValueError:
            data = {}
        return int(status), data if isinstance(data, dict) else {}

    def incoming(self, e164: str) -> tuple:
        status, data = self.list("IncomingPhoneNumbers", PhoneNumber=e164)
        rows = data.get("incoming_phone_numbers") or []
        return status, (rows[0] if rows and isinstance(rows[0], dict) else None)

    def verified_caller_id(self, e164: str) -> tuple:
        status, data = self.list("OutgoingCallerIds", PhoneNumber=e164)
        return status, bool(data.get("outgoing_caller_ids"))


def _has_voice(row: Optional[dict]) -> bool:
    caps = (row or {}).get("capabilities") or {}
    return caps.get("voice") is True or caps.get("Voice") is True


def run(load_config: Callable[[], object], probes: Probes) -> list[Item]:
    items: list[Item] = []
    add = lambda name, ok, text: items.append(Item(name, bool(ok), text))  # noqa: E731

    # 1. Hermes version
    try:
        raw = probes.hermes_version() or ""
    except Exception:
        raw = ""
    found = _version(raw)
    want = ".".join(map(str, MIN_HERMES))
    if found is None:
        add("Hermes version", False, f"could not read the Hermes version; Hermes Line needs {want} or newer")
    elif found < MIN_HERMES:
        add("Hermes version", False, f"{raw.strip()} is too old; update Hermes to {want} or newer (hermes update)")
    else:
        add("Hermes version", True, f"{raw.strip()} (needs {want} or newer)")

    # 2. Gateway
    try:
        running = bool(probes.gateway_running())
    except Exception:
        running = False
    add("Hermes gateway", running, "running" if running else
        "not running; start it with `hermes gateway start` (or `hermes gateway run` in a terminal)")

    # 3. Settings
    cfg = None
    try:
        cfg = load_config()
    except Exception as exc:
        add("Settings", False, f"plugins.entries.hermes-line.settings in config.yaml: {exc}")
    if cfg is not None:
        missing = [k for k in ("public_base_url", "from_number", "line_number", "owner_number")
                   if not getattr(cfg, k, "")]
        fixes = ([] if cfg.enabled else ["`enabled: true`"]) + missing
        if fixes:
            add("Settings", False, f"set {', '.join(fixes)} under plugins.entries.hermes-line.settings "
                "(Quick start, step 5)")
        else:
            add("Settings", True, "loaded, enabled: true")

    # 4. Credentials present (never printed)
    values: dict = {}
    absent, unreadable = [], []
    for name in REQUIRED_SECRETS:
        try:
            values[name] = probes.get_secret(name) or ""
        except Exception:
            values[name] = ""
            unreadable.append(name)
            continue
        if not values[name]:
            absent.append(name)
    account, key, secret = (values[n] for n in REQUIRED_SECRETS)
    if unreadable:
        add("Twilio credentials", False, f"{', '.join(unreadable)} could not be read from Hermes's secret store")
    elif absent:
        add("Twilio credentials", False, f"set {', '.join(absent)} in Hermes (`hermes plugins install` asks for "
            "them; README, setup step 1)")
    elif not _ACCOUNT.fullmatch(account) or not _KEY.fullmatch(key):
        bad = [n for n, ok in ((REQUIRED_SECRETS[0], _ACCOUNT.fullmatch(account)),
                               (REQUIRED_SECRETS[1], _KEY.fullmatch(key))) if not ok]
        add("Twilio credentials", False, f"{', '.join(bad)} has the wrong shape (Account SID starts with AC, API "
            "key SID with SK, then 32 hex characters)")
    else:
        add("Twilio credentials", True, "all three set (not shown)")
    creds_ok = items[-1].ok

    # 5-7. Twilio, read-only
    line_number = getattr(cfg, "line_number", "") if cfg is not None else ""
    from_number = getattr(cfg, "from_number", "") if cfg is not None else ""
    twilio = _Twilio(probes, account, key, secret) if creds_ok else None
    accepted = False
    if twilio is None:
        add("Twilio accepts the credentials", False, "skipped: fix the Twilio credentials first")
    else:
        try:
            status, _ = twilio.list("IncomingPhoneNumbers", PageSize="1")
        except OSError as exc:
            status = 0
            add("Twilio accepts the credentials", False, f"Twilio could not be reached ({type(exc).__name__}); "
                "check this machine's internet connection")
        if status == 200:
            accepted = True
            add("Twilio accepts the credentials", True, "a read-only request was accepted")
        elif status in (401, 403):
            add("Twilio accepts the credentials", False, f"Twilio refused them (HTTP {status}); the API key must "
                "belong to the account in HERMES_LINE_TWILIO_ACCOUNT_SID and its secret must match")
        elif status == 404:
            add("Twilio accepts the credentials", False, "Twilio does not know that Account SID (HTTP 404)")
        elif status:
            add("Twilio accepts the credentials", False, f"Twilio answered HTTP {status}; try again later")

    def number_row(e164: str):
        try:
            status, row = twilio.incoming(e164)
        except OSError as exc:
            return None, f"Twilio could not be reached ({type(exc).__name__})"
        return row, ("" if status == 200 else f"Twilio answered HTTP {status}")

    if not accepted:
        add("Line number on the account", False, "skipped: needs accepted Twilio credentials")
    elif not line_number:
        add("Line number on the account", False, "set line_number to the Twilio number you bought")
    else:
        row, err = number_row(line_number)
        if err:
            add("Line number on the account", False, err)
        elif row is None:
            add("Line number on the account", False, f"{ending(line_number)} is not a number on this Twilio "
                "account; buy a US local number with voice (Phone Numbers > Buy a number) and set line_number to it")
        elif not _has_voice(row):
            add("Line number on the account", False, f"{ending(line_number)} has no voice capability; use a "
                "number that can make and receive calls")
        else:
            add("Line number on the account", True, f"{ending(line_number)}, voice capable")

    if not accepted:
        add("Caller ID", False, "skipped: needs accepted Twilio credentials")
    elif not from_number:
        add("Caller ID", False, "set from_number to your Twilio number or a verified caller ID")
    elif from_number == line_number and items[-1].ok:
        add("Caller ID", True, f"{ending(from_number)}, the line number")
    else:
        row, err = number_row(from_number)
        verified = False
        if not err and row is None:
            try:
                status, verified = twilio.verified_caller_id(from_number)
                err = "" if status == 200 else f"Twilio answered HTTP {status}"
            except OSError as exc:
                err = f"Twilio could not be reached ({type(exc).__name__})"
        if err:
            add("Caller ID", False, err)
        elif row is not None and _has_voice(row):
            add("Caller ID", True, f"{ending(from_number)}, a voice number on this account")
        elif row is not None:
            add("Caller ID", False, f"{ending(from_number)} has no voice capability")
        elif verified:
            add("Caller ID", True, f"{ending(from_number)}, a verified caller ID on this account")
        else:
            add("Caller ID", False, f"{ending(from_number)} is neither a number on this account nor a verified "
                "caller ID; verify it in Twilio (Phone Numbers > Verified Caller IDs) or use the line number")

    # 8. Owner phone
    owner = getattr(cfg, "owner_number", "") if cfg is not None else ""
    if cfg is None:
        add("Owner phone", False, "skipped: fix the settings first")
    elif not owner:
        add("Owner phone", False, "set owner_number to your own mobile (E.164, e.g. +1 and ten digits)")
    else:
        try:
            allowed = bool(probes.destination_allowed(owner, dict(getattr(cfg, "policy", {}) or {})))
        except Exception:
            allowed = False
        if allowed:
            add("Owner phone", True, f"{ending(owner)}, on policy.allowed_destinations")
        else:
            add("Owner phone", False, f"{ending(owner)} is set but cannot be dialled: add it to "
                "policy.allowed_destinations")

    # 9. Call profile (the same check the plugin runs before every call)
    if cfg is None:
        add("Call profile", False, "skipped: fix the settings first")
    else:
        try:
            problems = list(probes.profile_problems(getattr(cfg, "call_profile", "")) or [])
        except Exception as exc:
            problems = [f"could not be checked ({type(exc).__name__})"]
        if problems:
            text = "; ".join(str(p) for p in problems)
            add("Call profile", False, text if "setup step" in text else f"{text} (README, setup step 4)")
        else:
            add("Call profile", True, f"{cfg.call_profile} passes the neutral-profile check")

    # 10. Tunnel
    base = getattr(cfg, "public_base_url", "") if cfg is not None else ""
    if not base:
        add("Tunnel", False, "set public_base_url to your tunnel's https:// origin (README, setup step 3)")
    else:
        try:
            status, body = probes.http_get(f"{base}/health", {"Accept": "application/json"}, TIMEOUT_S)
        except OSError as exc:
            status, body = 0, b""
            add("Tunnel", False, f"no HTTPS answer from public_base_url ({type(exc).__name__}); start the tunnel "
                "and check the hostname")
        if status:
            try:
                data = json.loads((body or b"").decode("utf-8", "replace") or "null")
            except ValueError:
                data = None
            if status == 200 and isinstance(data, dict) and "ok" in data:
                if data.get("ok") is True:
                    add("Tunnel", True, "public_base_url answers over HTTPS and reaches the ready call service")
                else:
                    add("Tunnel", False, "public_base_url reaches the plugin, but its call service is not ready; "
                        "run `/line service start` and see `/line service status`")
            else:
                add("Tunnel", False, f"public_base_url answered HTTP {status} but not from Hermes Line; point the "
                    "tunnel at listen_host:listen_port")

    # 11. Local port
    host = getattr(cfg, "listen_host", "127.0.0.1") if cfg is not None else "127.0.0.1"
    port = int(getattr(cfg, "listen_port", 8788) if cfg is not None else 8788)
    probe_host = "127.0.0.1" if host in ("", "0.0.0.0") else ("::1" if host == "::" else host)
    try:
        listening = bool(probes.port_open(probe_host, port, 2.0))
    except Exception:
        listening = False
    add("Local port", listening, f"{host}:{port} is listening" if listening else
        f"nothing listens on {host}:{port}; the call service runs in the Hermes gateway when `enabled: true` "
        "(start the gateway, then `/line service status`)")
    return items


def report(items: list[Item]) -> str:
    passed = sum(1 for i in items if i.ok)
    tail = ("All checks passed. No call was made." if passed == len(items) else
            f"{len(items) - passed} of {len(items)} checks need a fix. No call was made.")
    return "\n".join(["Hermes Line setup check (read-only: no call, no text, nothing bought)"]
                     + [i.line() for i in items] + [tail])


def exit_code(items: list[Item]) -> int:
    return 0 if items and all(i.ok for i in items) else 1


# -- the real probes (used only inside Hermes) ------------------------------------------------------------------
def http_get(url: str, headers: dict, timeout: float) -> tuple:
    """A GET with the standard library (TLS verified). An HTTP error status is returned, not raised."""
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(65536)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(65536)
        except Exception:
            body = b""
        return exc.code, body
    except urllib.error.URLError as exc:
        reason = exc.reason if isinstance(exc.reason, OSError) else None
        raise (reason or OSError("unreachable")) from None


def port_open(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def hermes_version() -> str:
    """The version Hermes's public CLI prints (``hermes --version``): the ``hermes`` next to this interpreter first,
    then the one on PATH. It gets only a minimal environment. Empty when neither answers."""
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path
    env = {k: os.environ[k] for k in ("PATH", "HOME", "HERMES_HOME", "LANG", "TMPDIR") if k in os.environ}
    for exe in (Path(sys.executable).parent / "hermes", shutil.which("hermes")):
        if not exe or not Path(exe).is_file():
            continue
        try:
            out = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=30, env=env,
                                 stdin=subprocess.DEVNULL).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        m = re.search(r"\bv?(\d+\.\d+(?:\.\d+)?)\b", out or "")
        if m:
            return m.group(1)
    return ""


def gateway_running() -> bool:
    from gateway.status import get_running_pid
    return get_running_pid(cleanup_stale=False) is not None


def destination_allowed(e164: str, policy: dict) -> bool:
    from . import deps
    return bool(deps.check_destination(e164, deps.Settings.load(policy)).allowed)
