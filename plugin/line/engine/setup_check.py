"""No-cost setup check: ``/line check`` in a Hermes chat, ``hermes hermes-line check`` in a shell.

It places no call and sends no text. Twilio is asked only with read-only GET requests (the account's number
list, filtered to the configured numbers, and its verified caller IDs); nothing is created, changed or bought.
Every item prints one plain line, ``OK`` or ``FIX`` with what to do. Credentials and SIDs are never printed;
a phone number is shown only by its last two digits; no exception text is shown, only fixed or allowlisted
messages and exception class names. Redirects are never followed. ``exit_code`` is 0 only when every item passes.

Each probe is injected (``Probes``), so the tests run with fakes and no network.
"""
from __future__ import annotations

import base64
import http.client
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, fields
from typing import Callable, Optional

MIN_HERMES = (0, 22, 5)
TWILIO_API = "https://api.twilio.com/2010-04-01"
TIMEOUT_S = 10.0
MAX_BODY = 1 << 20
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
    gateway_running: Callable[[], bool]                # a gateway process for this Hermes home is running
    get_secret: Callable[[str], Optional[str]]         # None when unset; raises when it cannot be read
    http_get: Callable[[str, dict, float], tuple]      # (url, headers, timeout) -> (status, body bytes); raises
    port_open: Callable[[str, int, float], bool]
    profile_problems: Callable[[str], list]            # the call-profile check the plugin runs before each call
    destination_allowed: Callable[[str, dict], bool]   # (e164, policy settings) -> on the dial allowlist


class ProbeError(OSError):
    """A request that got no usable answer. Its text is only a fixed kind (an exception class name or a short
    fixed phrase), never the original exception's message, which may hold a URL, a SID or a number."""

    def __init__(self, kind: str):
        kind = kind if re.fullmatch(r"[A-Za-z][A-Za-z0-9 _-]{0,40}", kind or "") else "error"
        super().__init__(kind)
        self.kind = kind


def _kind(exc: BaseException) -> str:
    return exc.kind if isinstance(exc, ProbeError) else type(exc).__name__


def ending(e164: str) -> str:
    """A number as the check prints it: its last two digits only."""
    digits = re.sub(r"\D", "", e164 or "")
    return f"number ending in {digits[-2:]}" if len(digits) >= 2 else "number"


def _version(text: str) -> Optional[tuple]:
    m = re.match(r"\s*v?(\d{1,4})\.(\d{1,4})(?:\.(\d{1,4}))?", text or "")
    return tuple(int(g or 0) for g in m.groups()) if m else None


def _safe(text: str) -> bool:
    """Text built from settings or plugin messages may be shown only when it cannot hold a number, SID, secret,
    URL or address: no run of 5+ digits, no 16+ character word, no ``@``, ``://`` or ``+`` followed by a digit."""
    text = str(text or "")
    return (len(text) <= 200 and not re.search(r"\d{5}", text) and not re.search(r"[A-Za-z0-9_-]{16,}", text)
            and "@" not in text and "＠" not in text and "://" not in text and not re.search(r"\+\d", text))


_SETTING_NAMES: Optional[frozenset] = None


def _setting_names() -> frozenset:
    global _SETTING_NAMES
    if _SETTING_NAMES is None:
        from .config import EngineConfig
        _SETTING_NAMES = frozenset(f.name for f in fields(EngineConfig))
    return _SETTING_NAMES


def settings_message(exc: BaseException) -> str:
    """What the Settings item says about a settings error: the loader's own message only when it is a validation
    message (``ValueError``) that starts with a known setting name (or ``policy:``, ``setting ``, ``unknown
    hermes-line settings:``) and passes ``_safe``; otherwise a fixed sentence."""
    fixed = ("the settings do not load; compare plugins.entries.hermes-line.settings with the README Settings "
             "table")
    if not isinstance(exc, ValueError):
        return fixed
    text = str(exc)
    head = re.match(r"([a-z_]+)", text)
    known = (text.startswith(("policy: ", "setting ", "unknown hermes-line settings: "))
             or bool(head and head.group(1) in _setting_names()))
    return f"{text} (plugins.entries.hermes-line.settings)" if known and _safe(text) else fixed


class _Twilio:
    """Read-only Twilio REST: GET only, account-scoped list resources. Any answer that is not the expected shape
    raises ``ProbeError``."""

    def __init__(self, probes: Probes, account: str, key: str, secret: str):
        self._get = probes.http_get
        self._base = f"{TWILIO_API}/Accounts/{account}"
        token = base64.b64encode(f"{key}:{secret}".encode()).decode()
        self._headers = {"Authorization": f"Basic {token}", "Accept": "application/json"}

    def list(self, resource: str, **query: str) -> tuple:
        """(HTTP status, parsed JSON object or {} when the status is not 200)."""
        url = f"{self._base}/{resource}.json?{urllib.parse.urlencode(query)}"
        status, body = self._get(url, dict(self._headers), TIMEOUT_S)
        if not isinstance(status, int) or isinstance(status, bool):
            raise ProbeError("unexpected answer")
        if status != 200:
            return status, {}
        try:
            data = json.loads(bytes(body or b"").decode("utf-8"))
        except (ValueError, TypeError):
            raise ProbeError("unexpected answer") from None
        if not isinstance(data, dict):
            raise ProbeError("unexpected answer")
        return status, data

    @staticmethod
    def _rows(data: dict, key: str) -> list:
        rows = data.get(key)
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise ProbeError("unexpected answer")
        return rows

    def incoming(self, e164: str) -> tuple:
        status, data = self.list("IncomingPhoneNumbers", PhoneNumber=e164)
        if status != 200:
            return status, None
        rows = [r for r in self._rows(data, "incoming_phone_numbers") if r.get("phone_number") == e164]
        if rows and not isinstance(rows[0].get("capabilities"), dict):
            raise ProbeError("unexpected answer")
        return status, (rows[0] if rows else None)

    def verified_caller_id(self, e164: str) -> tuple:
        status, data = self.list("OutgoingCallerIds", PhoneNumber=e164)
        if status != 200:
            return status, False
        return status, any(r.get("phone_number") == e164 for r in self._rows(data, "outgoing_caller_ids"))


def _has_voice(row: Optional[dict]) -> bool:
    caps = (row or {}).get("capabilities")
    return isinstance(caps, dict) and (caps.get("voice") is True or caps.get("Voice") is True)


def run(load_config: Callable[[], object], probes: Probes) -> list[Item]:
    items: list[Item] = []
    add = lambda name, ok, text: items.append(Item(name, bool(ok), text))  # noqa: E731

    # 1. Hermes version (only the parsed digits are ever shown)
    try:
        found = _version(str(probes.hermes_version() or ""))
    except Exception:
        found = None
    want = ".".join(map(str, MIN_HERMES))
    if found is None:
        add("Hermes version", False, f"could not read the Hermes version (`hermes --version`); Hermes Line needs "
            f"{want} or newer")
    elif found < MIN_HERMES:
        add("Hermes version", False, f"{'.'.join(map(str, found))} is too old; update Hermes to {want} or newer "
            "(hermes update)")
    else:
        add("Hermes version", True, f"{'.'.join(map(str, found))} (needs {want} or newer)")

    # 2. Gateway process. Only its process record is read (Hermes's PID and lock files); whether it answers chats
    # is not probed, so the item says "process".
    try:
        running = bool(probes.gateway_running())
    except Exception:
        running = False
    add("Hermes gateway process", running, "running (process found; whether it answers chats is not tested)"
        if running else "not running; start it with `hermes gateway start` (or `hermes gateway run` in a terminal)")

    # 3. Settings
    cfg = None
    try:
        cfg = load_config()
    except Exception as exc:
        add("Settings", False, settings_message(exc))
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
            value = probes.get_secret(name)
            values[name] = value if isinstance(value, str) else ""
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

    def unreachable(exc: BaseException) -> str:
        return f"Twilio gave no usable answer ({_kind(exc)}); check this machine's internet connection and retry"

    accepted = False
    if twilio is None:
        add("Twilio accepts the credentials", False, "skipped: fix the Twilio credentials first")
    else:
        try:
            status, _ = twilio.list("IncomingPhoneNumbers", PageSize="1")
        except Exception as exc:
            status = 0
            add("Twilio accepts the credentials", False, unreachable(exc))
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
        except Exception as exc:
            return None, unreachable(exc)
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
            except Exception as exc:
                err = unreachable(exc)
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
            problems = [str(p) for p in list(probes.profile_problems(getattr(cfg, "call_profile", "")) or [])]
        except Exception as exc:
            problems = [f"could not be checked ({_kind(exc)})"]
        if problems:
            text = "; ".join(p if _safe(p) else "a problem the check could not show safely" for p in problems)
            add("Call profile", False, text if "setup step" in text else f"{text} (README, setup step 4)")
        else:
            add("Call profile", True, f"{cfg.call_profile} passes the neutral-profile check")

    # 10. Tunnel (no redirect is followed: the answer must come from public_base_url itself, over HTTPS)
    base = getattr(cfg, "public_base_url", "") if cfg is not None else ""
    if not base:
        add("Tunnel", False, "set public_base_url to your tunnel's https:// origin (README, setup step 3)")
    elif urllib.parse.urlsplit(base).scheme != "https":
        add("Tunnel", False, "public_base_url must start with https://")
    else:
        try:
            status, body = probes.http_get(f"{base}/health", {"Accept": "application/json"}, TIMEOUT_S)
        except Exception as exc:
            status, body = 0, b""
            add("Tunnel", False, f"no HTTPS answer from public_base_url ({_kind(exc)}); start the tunnel and check "
                "the hostname")
        if status:
            try:
                data = json.loads(bytes(body or b"").decode("utf-8") or "null")
            except (ValueError, TypeError):
                data = None
            if isinstance(status, int) and 300 <= status < 400:
                add("Tunnel", False, f"public_base_url redirected (HTTP {status}); the check does not follow "
                    "redirects: point the tunnel straight at listen_host:listen_port")
            elif status == 200 and isinstance(data, dict) and "ok" in data:
                if data.get("ok") is True:
                    add("Tunnel", True, "public_base_url answers over HTTPS and reaches the ready call service")
                else:
                    add("Tunnel", False, "public_base_url reaches the plugin, but its call service is not ready; "
                        "run `/line service start` and see `/line service status`")
            else:
                add("Tunnel", False, f"public_base_url answered HTTP {status} but not from Hermes Line; point the "
                    "tunnel at listen_host:listen_port")

    # 11. Local port
    host = str(getattr(cfg, "listen_host", "127.0.0.1") if cfg is not None else "127.0.0.1")
    try:
        port = int(getattr(cfg, "listen_port", 8788) if cfg is not None else 8788)
    except (TypeError, ValueError):
        port = 0
    shown = host if _safe(host) else "listen_host"
    if not 1 <= port <= 65535:
        add("Local port", False, "listen_port is not a fixed port (0 or unset in these settings), so nothing can be "
            "checked; set listen_port (default 8788) and point the tunnel at it")
        return items
    probe_host = "127.0.0.1" if host in ("", "0.0.0.0") else ("::1" if host == "::" else host)
    try:
        listening = bool(probes.port_open(probe_host, port, 2.0))
    except Exception:
        listening = False
    add("Local port", listening, f"{shown}:{port} is listening" if listening else
        f"nothing listens on {shown}:{port}; the call service runs in the Hermes gateway when `enabled: true` "
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
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: the 3xx comes back as the answer, and no header (Authorization included) is sent
    to any other address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opener(extra_handlers: tuple = ()) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_NoRedirect, *extra_handlers)


def http_get(url: str, headers: dict, timeout: float, *, _handlers: tuple = ()) -> tuple:
    """A GET with the standard library: TLS verified, redirects not followed, only ``https`` URLs. An HTTP status
    (3xx included) is returned with at most 1 MiB of body; a failure raises ``ProbeError`` that names only the
    exception class, never its message."""
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ProbeError("not https")
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with _opener(_handlers).open(req, timeout=timeout) as resp:
            return int(resp.status), resp.read(MAX_BODY)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(MAX_BODY)
        except Exception:
            body = b""
        return int(exc.code), body
    except urllib.error.URLError as exc:
        reason = exc.reason
        raise ProbeError(type(reason).__name__ if isinstance(reason, BaseException) else "URLError") from None
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise ProbeError(type(exc).__name__) from None


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
    """Hermes's own process check for this home's gateway (PID and lock records). It does not test whether the
    gateway answers; Hermes offers no read-only responsiveness probe for plugins."""
    from gateway.status import get_running_pid
    return get_running_pid(cleanup_stale=False) is not None


def destination_allowed(e164: str, policy: dict) -> bool:
    from . import deps
    return bool(deps.check_destination(e164, deps.Settings.load(policy)).allowed)
