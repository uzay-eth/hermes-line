"""Offline test harness: fake Twilio adapter, scripted Hermes driver, Twilio simulator.

All numbers are SAMPLE numbers (555-01xx). Nothing here touches a network
beyond 127.0.0.1, and no real model, Twilio account or phone is used.
"""
from __future__ import annotations

import sys
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[2]))  # repo root: import the plugin as ``plugin``

import json
import re
import secrets
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from websockets.sync.client import connect as ws_connect

from plugin.line.engine.config import EngineConfig
from plugin.line.engine.contracts import CallState
from plugin.line.engine.driver import TurnResult
from plugin.line.engine.service import CallService
from plugin.line.engine.telephony import TelephonyAdapter
from plugin.line.server import twilio_signature
from plugin.line.store import Store

BUSINESS = "+17755550101"   # sample business
OWNER = "+17755550199"      # sample owner phone
LINE = "+17755550100"       # sample Twilio number
OTHER = "+17755550123"      # sample number that is NOT on the allowlist
AUTH_TOKEN = "test-auth-token-not-a-secret"
ACCOUNT = "AC" + "0" * 32
PUBLIC = "https://line.test"
SECRETS = {"HERMES_LINE_TWILIO_AUTH_TOKEN": AUTH_TOKEN, "HERMES_LINE_TWILIO_ACCOUNT_SID": ACCOUNT}


def wait_for(cond: Callable[[], Any], timeout: float = 5.0, step: float = 0.01, what: str = "condition") -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = cond()
        if value:
            return value
        time.sleep(step)
    raise AssertionError(f"timed out waiting for {what}")


class FakeAdapter(TelephonyAdapter):
    """Records every provider primitive. ``place_call`` (the gate) is inherited unchanged."""

    def __init__(self) -> None:
        super().__init__()
        self.created: list[dict] = []
        self.ended: list[str] = []
        self.redirected: list[tuple[str, str]] = []
        self.fail_create = False
        # Twilio's REST view of legs (fetch_call), for the no-Auth-Token ingress mode.
        self.records: dict[str, dict] = {}
        self.rest_down = False
        self.fetches: list[str] = []

    @property
    def total_calls(self) -> int:
        return len(self.created) + len(self.ended) + len(self.redirected)

    def _create_call(self, *, to, from_number, answer_url, status_url, timeout_s, time_limit_s) -> str:
        if self.fail_create:
            raise RuntimeError("provider down")
        sid = "CA" + secrets.token_hex(16)
        self.created.append({"sid": sid, "to": to, "from": from_number, "answer_url": answer_url,
                             "status_url": status_url, "timeout_s": timeout_s, "time_limit_s": time_limit_s})
        self.records[sid] = {"sid": sid, "account_sid": ACCOUNT, "direction": "outbound-api", "from": from_number,
                             "to": to, "status": "in-progress"}
        return sid

    def add_inbound(self, sid: str, from_: str, to: str, *, account: str = None, status: str = "in-progress") -> None:
        self.records[sid] = {"sid": sid, "account_sid": account or ACCOUNT, "direction": "inbound",
                             "from": from_, "to": to, "status": status}

    def fetch_call(self, sid: str):
        self.fetches.append(sid)
        if self.rest_down:
            raise RuntimeError("Twilio unreachable")
        record = self.records.get(sid)
        return dict(record) if record else None

    def end_call(self, sid: str) -> None:
        self.ended.append(sid)

    def redirect_call(self, sid: str, url: str) -> None:
        self.redirected.append((sid, url))


class FakeDriver:
    """Scripted stand-in for the Hermes call session.

    ``reply(driver, text)`` returns the chunks to stream (may call in-call tools through
    ``driver.tool(...)``); ``ask(prompt)`` answers line.detect's model questions.
    """

    instances: list["FakeDriver"] = []

    def __init__(self, session_id: str, svc_ref: dict, reply: Callable, ask: Callable, chunk_delay: float = 0.0):
        self.session_id = session_id
        self.svc_ref = svc_ref
        self.reply = reply
        self.ask_fn = ask
        self.chunk_delay = chunk_delay
        self.briefing = ""
        self.turns: list[str] = []
        self.asks: list[str] = []
        self.notes: list[str] = []
        self.history: list[dict] = []
        self.interrupts: list[Optional[str]] = []
        self.closed = False
        self._interrupted = threading.Event()
        FakeDriver.instances.append(self)

    def tool(self, method: str, *args) -> dict:
        return self.svc_ref["svc"].in_call(self.session_id, method, *args)

    def start(self, briefing: str) -> None:
        self.briefing = briefing

    def set_mcp_servers(self, servers: list) -> None:  # round 6: what AcpDriver would hand the gateway
        self.mcp_servers = servers

    def turn(self, text: str, emit) -> TurnResult:
        self.turns.append(text)
        self._interrupted.clear()
        chunks = list(self.reply(self, text) or [])
        sent = []
        for chunk in chunks:
            if self._interrupted.is_set():
                break
            emit(chunk)
            sent.append(chunk)
            if self.chunk_delay:
                self._interrupted.wait(self.chunk_delay)
        interrupted = self._interrupted.is_set()
        self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": "".join(sent)}]
        return TurnResult(text="".join(sent), interrupted=interrupted, first_text_ms=1.0, full_ms=2.0)

    def interrupt(self, spoken_prefix=None) -> None:
        self.interrupts.append(spoken_prefix)
        self._interrupted.set()

    def ask(self, prompt: str, timeout_s: float = 20.0) -> str:
        self.asks.append(prompt)
        return self.ask_fn(prompt)

    def note(self, text: str) -> None:
        self.notes.append(text)

    def close(self) -> None:
        self.closed = True


def human_if_confirmed(prompt: str) -> str:
    """Fake model for line.detect: says human (with valid evidence) only after Hermes asked."""
    if prompt.startswith("The phone call has ended"):
        return "The booking is confirmed for Friday at 7 pm."
    try:
        data = json.loads(prompt.split("\nReply with only")[0])
    except ValueError:
        return ""
    turns = data.get("turns", [])
    question = [t["index"] for t in turns if t["speaker"] == "hermes" and "live person" in t["text"]]
    if question:
        return json.dumps({"label": "human", "confidence": "high", "evidence": [question[-1], len(turns) - 1],
                           "contingent": True})
    return json.dumps({"label": "unknown", "confidence": "low", "evidence": [len(turns) - 1]})


def say_hello(driver, text):
    return ["Hi! ", "I'd like to ", "book a table."]


class Line:
    """One running call service on loopback with fakes, plus a Twilio simulator."""

    def __init__(self, *, reply: Callable = say_hello, ask: Callable = human_if_confirmed, policy: Optional[dict] = None,
                 now_utc=None, chunk_delay: float = 0.0, start: bool = True, store_dir: Optional[Path] = None,
                 driver_factory: Optional[Callable] = None, secrets: Optional[dict] = None,
                 voice_factories: Optional[dict] = None, **cfg_over):
        self.dir = Path(store_dir) if store_dir else Path(tempfile.mkdtemp(prefix="hl-l1-"))
        self._own_dir = store_dir is None
        pol = {"allowed_destinations": [BUSINESS, OWNER], "quiet_hours_enabled": False}
        pol.update(policy or {})
        values = dict(enabled=True, public_base_url=PUBLIC, from_number=LINE, line_number=LINE, owner_number=OWNER,
                      owner_name="Sam", relay_reconnect_grace_s=0.3, confirm_cooldown_s=0.0, speech_chars_per_s=1000.0,
                      ask_timeout_s=2.0, turn_timeout_s=5.0, policy=pol,
                      quiet_before_speak_s=0.0)  # round 30: tests opt in to the real 1.2 s quiet
        values.update(cfg_over)
        self.cfg = EngineConfig.load(values)
        self.store = Store(self.dir / "line.db")
        self.adapter = FakeAdapter()
        self.delivered: list[tuple[Optional[str], str]] = []
        self.events: list[tuple[str, int]] = []
        self.ref: dict = {}
        self.drivers: list[FakeDriver] = []

        def factory(session_id: str) -> FakeDriver:
            drv = FakeDriver(session_id, self.ref, reply, ask, chunk_delay)
            self.drivers.append(drv)
            return drv

        def sink(task, row):
            self.delivered.append((task.id if task else None, row["text"]))
            return True, ""

        if driver_factory is not None:
            factory = driver_factory
        kwargs = {}
        if now_utc is not None:
            kwargs["now_utc"] = now_utc
        self.svc = CallService(cfg=self.cfg, store=self.store, adapter=self.adapter, driver_factory=factory,
                               lock_dir=self.dir / "locks", get_secret=(SECRETS if secrets is None else secrets).get,
                               summary_sink=sink,
                               notifier=lambda cid, rev: self.events.append((cid, rev)), tick_s=0.02,
                               voice_factories=voice_factories, **kwargs)
        self.ref["svc"] = self.svc
        if start:
            self.svc.start(port=0)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.svc.server.port}"

    def close(self) -> None:
        try:
            self.svc.stop(timeout=5)
        finally:
            self.store.close()
            if self._own_dir:
                shutil.rmtree(self.dir, ignore_errors=True)

    # -- Twilio simulator -------------------------------------------------------
    def post(self, path: str, params: dict, *, sign: bool = True, token: str = AUTH_TOKEN,
             signature: Optional[str] = None, account: Optional[str] = ACCOUNT) -> tuple[int, str]:
        params = dict(params)
        if path.startswith("/twilio/"):
            path = self.svc.auth.prefix() + path  # every Twilio URL carries the per-install path secret
        if account is not None:
            params.setdefault("AccountSid", account)
        pairs = list(params.items())
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if sign:
            headers["X-Twilio-Signature"] = signature or twilio_signature.compute(token, PUBLIC + path, pairs)
        req = urllib.request.Request(self.base + path, data=urllib.parse.urlencode(pairs).encode(), headers=headers,
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    def request(self, *, mode: str = "agent", destination: str = BUSINESS, **kw) -> dict:
        return self.svc.request_call(destination=destination, title=kw.pop("title", "Book a table for 4 at 7"),
                                     goal=kw.pop("goal", "Book a table for four at 7 pm Friday."), mode=mode, **kw)

    def approve(self, task_id: str, source: str = "slash") -> dict:
        return self.svc.submit("approve", source=source, task_id=task_id)

    def dial(self, *, mode: str = "agent", **kw) -> tuple[dict, dict]:
        req = self.request(mode=mode, **kw)
        out = self.approve(req["task_id"])
        assert out["result"]["ok"], out
        created = wait_for(lambda: self.adapter.created and self.adapter.created[-1], what="dial")
        wait_for(lambda: self.session(req["call_id"]) and self.session(req["call_id"]).provider_sid)
        return req, created

    def session(self, call_id: str):
        return self.svc.sessions.get(call_id)

    def state(self, call_id: str) -> str:
        return self.store.get_call(call_id).state.value

    def wait_state(self, call_id: str, state: str, timeout: float = 5.0) -> None:
        wait_for(lambda: self.state(call_id) == state, timeout, what=f"state {state} (now {self.state(call_id)})")

    def wait_task(self, task_id: str, status: str, timeout: float = 5.0) -> None:
        wait_for(lambda: self.store.get_task(task_id).status.value == status, timeout,
                 what=f"task {status} (now {self.store.get_task(task_id).status.value})")

    def answer(self, call_id: str, sid: str) -> str:
        status, body = self.post(f"/twilio/voice/answer/{call_id}", {"CallSid": sid, "CallStatus": "in-progress"})
        assert status == 200, (status, body)
        return body

    def status(self, call_id: str, sid: str, status: str, *, owner: bool = False) -> int:
        path = f"/twilio/voice/owner/status/{call_id}" if owner else f"/twilio/voice/status/{call_id}"
        return self.post(path, {"CallSid": sid, "CallStatus": status})[0]

    def relay(self, twiml_body: str, *, sign: bool = True, signature: Optional[str] = None) -> "Relay":
        url = re.search(r'url="([^"]+)"', twiml_body).group(1).replace("&amp;", "&")
        return Relay(self, url, sign=sign, signature=signature)

    def connect(self, req: dict, created: dict, *, setup: bool = True) -> "Relay":
        body = self.answer(req["call_id"], created["sid"])
        relay = self.relay(body)
        if setup:
            relay.send({"type": "setup", "sessionId": "VX" + "1" * 32, "callSid": created["sid"],
                        "from": LINE, "to": created["to"], "direction": "outbound-api"})
        return relay


class Relay:
    """A fake ConversationRelay client speaking the WebSocket protocol."""

    def __init__(self, line: Line, public_url: str, *, sign: bool = True, signature: Optional[str] = None):
        self.public_url = public_url
        local = public_url.replace("wss://line.test", f"ws://127.0.0.1:{line.svc.server.port}")
        headers = {}
        if sign:
            headers["X-Twilio-Signature"] = signature or twilio_signature.compute(AUTH_TOKEN, public_url, [])
        self.ws = ws_connect(local, additional_headers=headers, open_timeout=5, close_timeout=2)
        self.frames: list[dict] = []
        self._lock = threading.Lock()
        self.closed = threading.Event()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        try:
            for message in self.ws:
                with self._lock:
                    self.frames.append(json.loads(message))
        except Exception:
            pass
        finally:
            self.closed.set()

    def send(self, frame: dict) -> None:
        self.ws.send(json.dumps(frame))

    def prompt(self, text: str) -> None:
        self.send({"type": "prompt", "voicePrompt": text, "lang": "en-US", "last": True})

    def texts(self) -> list[str]:
        with self._lock:
            return [f.get("token", "") for f in self.frames if f.get("type") == "text"]

    def spoken(self) -> str:
        return "".join(self.texts())

    def of_type(self, kind: str) -> list[dict]:
        with self._lock:
            return [f for f in self.frames if f.get("type") == kind]

    def wait_text(self, needle: str, timeout: float = 5.0) -> None:
        wait_for(lambda: needle in self.spoken(), timeout, what=f"relay text {needle!r} in {self.spoken()!r}")

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


def get_state_after(line: Line, call_id: str) -> CallState:
    return line.store.get_call(call_id).state
