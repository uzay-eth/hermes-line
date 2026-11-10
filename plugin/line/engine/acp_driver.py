"""Hermes call driver over ACP (``hermes acp``): the public, documented agent protocol.

The plugin's call driver. Interface ``driver.CallDriver`` (``start / turn / interrupt /
close`` plus ``ask`` and ``note``); no Hermes module is imported: the plugin spawns the
``hermes acp`` CLI and speaks the Agent Client Protocol (JSON-RPC 2.0, one JSON
object per line on stdio). In Hermes v0.22.5 that process is a viewer: the
profile's gateway owns the session, so model route, credentials, memory, SOUL,
tools and approvals are exactly the call profile's own. The plugin starts it with
Hermes's ``--ignore-rules`` (no SOUL.md, context files, memory or preloaded skills),
refuses a call profile that holds the owner's memory, persona or prompt settings
(``call_profile.py``), gives the child an exact environment allow-list
(``child_env``) and runs the session in a fresh empty directory under a temp root that has no git repository or
project marker at or above it (``_workdir``, ``clean_temp_root``).

Differences from an in-process (embedded) agent, by protocol:
- The call briefing is sent with the first prompt (ACP has no system-prompt field).
- History lives in the gateway session and cannot be rewritten. On barge-in the
  turn is cancelled (``session/cancel``) and the next prompt starts with what the
  caller actually heard, so the agent never assumes an unsaid sentence was heard.
- ``ask`` (line.detect's model) uses a second ACP session so the call history
  stays clean.
- The session is persisted by Hermes like any ACP session (state.db, session
  search). Only masked transcript text is sent to it (see machine.py).
- Reasoning effort is the profile's; ACP has no per-session reasoning control.
- The agent's permission requests (dangerous commands, edits) are always
  declined: nobody can approve them from a phone call.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable, Optional

from .driver import TurnResult

PROTOCOL_VERSION = 1
MAX_LINE = 8 * 1024 * 1024


class AcpError(RuntimeError):
    pass


def default_command(profile: str = "") -> list[str]:
    """``hermes [--profile NAME] acp`` (or ``hermes-acp``): both documented entry points of the ACP adapter.
    Round 8: ``profile`` selects the Hermes profile the call sessions run in (its toolsets bound them)."""
    exe = shutil.which("hermes")
    if exe:
        return [exe, *(["--profile", profile] if profile else []), "acp"]
    exe = shutil.which("hermes-acp")
    if exe and not profile:
        return [exe]
    raise AcpError("`hermes` is not on PATH" if profile else "neither `hermes` nor `hermes-acp` is on PATH")


# Launch flags `hermes acp` would freeze into the gateway session from its environment
# (``hermes_cli.gateway_chat._ENV_LAUNCH_FLAGS``). A call session never inherits them (round 8, catalog rule 12):
# no YOLO, no ignored rules, no pre-accepted hooks, no safe-mode freeze.
INHERITED_LAUNCH_FLAGS = ("HERMES_YOLO_MODE", "HERMES_IGNORE_RULES", "HERMES_SAFE_MODE", "HERMES_IGNORE_USER_CONFIG",
                          "HERMES_ACCEPT_HOOKS")


# Client-environment names Hermes carries into a session's prompt or plugins (``agent.session_client_env``
# ``BEHAVIOUR_ENV``, the environment hint, the TUI skill preload). A call session never gets them.
PROMPT_ENV = ("HERMES_EPHEMERAL_SYSTEM_PROMPT", "HERMES_PREFILL_MESSAGES_FILE", "HERMES_ENVIRONMENT_HINT",
              "HERMES_ENABLE_PROJECT_PLUGINS", "HERMES_TUI_SKILLS")
# The exact allow-list for the viewer when the plugin passes no env (Nous review of d92bc1fb): what `hermes acp`
# needs to run and to find the profile (HERMES_HOME), and, when it starts the call profile's gateway, what that
# gateway needs to reach the model provider (proxy and CA bundle; a proxy URL passes as set, credentials included).
# No provider keys, no toolchain, no other Hermes variables: the call profile holds its own provider key (README,
# setup step 4). A call gateway that is already running keeps the environment it was started with.
CHILD_ENV_NAMES = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "TMPDIR",
                   "HERMES_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME",
                   "XDG_RUNTIME_DIR", "SYSTEMROOT", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
                   "CURL_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "http_proxy",
                   "https_proxy", "no_proxy", "all_proxy")


def child_env(env: Optional[dict]) -> dict:
    """The viewer's environment: the given one, or the ``CHILD_ENV_NAMES`` part of this process's, without the
    launch flags and prompt names above. Values are copied, never read or logged."""
    if env is None:
        base = {k: v for k, v in os.environ.items() if k in CHILD_ENV_NAMES}
    else:
        base = dict(env)
    for name in INHERITED_LAUNCH_FLAGS + PROMPT_ENV:
        base.pop(name, None)
    return base


# Hermes's project-root signals (``agent.coding_context._PROJECT_MARKERS``) and its git rule (``.git`` in the session
# cwd or any parent). A session cwd under either gets Hermes's coding workspace block (root, branch, status, recent
# commit subjects) even with --ignore-rules, which only turns the context files off (S7 delta review R1).
PROJECT_MARKERS = (
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "package.json", "tsconfig.json", "deno.json",
    "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts", "Gemfile", "composer.json", "mix.exs",
    "pubspec.yaml", "CMakeLists.txt", "Makefile", "Dockerfile", "AGENTS.md", "CLAUDE.md", ".cursorrules",
)
# Fixed system temp roots, tried after this process's temp dir (which follows TMPDIR, and which Hermes may point
# at its home's cache/scratch).
TEMP_ROOTS = ("/tmp", "/var/tmp")


def workspace_signal(path: str) -> Optional[str]:
    """The first ``.git`` or project marker at ``path`` or any of its parents, else None. Stricter than Hermes's
    ``_git_root``/``_marker_root``: markers are checked at every level, and ``$HOME`` and the temp root are not
    skipped (the call gateway's own home and temp root can differ from this process's)."""
    current = os.path.realpath(path)
    while True:
        for name in (".git",) + PROJECT_MARKERS:
            if os.path.lexists(os.path.join(current, name)):
                return os.path.join(current, name)
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def clean_temp_root() -> str:
    """The first of this process's temp dir, ``/tmp`` and ``/var/tmp`` that exists and has no ``workspace_signal``;
    raises ``AcpError`` when none qualifies, so no call session starts inside a project."""
    for candidate in (tempfile.gettempdir(), *TEMP_ROOTS):
        try:
            root = os.path.realpath(candidate)
        except (OSError, ValueError):
            continue
        if os.path.isdir(root) and workspace_signal(root) is None:
            return root
    raise AcpError("no temp directory outside a git repository or project (this process's temp dir, /tmp and "
                   "/var/tmp were checked): a call session there would get that project's workspace in its prompt")


class _Connection:
    """Minimal JSON-RPC peer over a child process's stdio."""

    def __init__(self, command: list[str], env: Optional[dict], cwd: Optional[str]):
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=env, cwd=cwd, bufsize=0)
        self._next = 0
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._pending: dict[int, "queue.Queue"] = {}
        self.on_notification: Callable[[str, dict], None] = lambda method, params: None
        self.on_request: Callable[[str, dict], Any] = lambda method, params: None
        self.closed = threading.Event()
        self._reader = threading.Thread(target=self._read, name="hermes-line-acp", daemon=True)
        self._reader.start()

    def _send(self, message: dict) -> None:
        data = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        with self._write_lock:
            if self.proc.stdin is None or self.closed.is_set():
                raise AcpError("ACP connection closed")
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise AcpError("ACP connection closed") from exc

    def request(self, method: str, params: dict, timeout: float) -> dict:
        with self._lock:
            self._next += 1
            rid = self._next
            box: "queue.Queue" = queue.Queue(maxsize=1)
            self._pending[rid] = box
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            message = box.get(timeout=timeout)
        except queue.Empty:
            raise AcpError(f"{method} timed out") from None
        finally:
            with self._lock:
                self._pending.pop(rid, None)
        if "error" in message:
            err = message["error"] or {}
            raise AcpError(f"{method} failed: {str(err.get('message', ''))[:200]}")
        return message.get("result") or {}

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _read(self) -> None:
        stream = self.proc.stdout
        try:
            while stream is not None:
                line = stream.readline(MAX_LINE)
                if not line:
                    break
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                if "method" in message and "id" in message:
                    self._answer(message)
                elif "method" in message:
                    try:
                        self.on_notification(message["method"], message.get("params") or {})
                    except Exception:
                        pass
                elif "id" in message:
                    with self._lock:
                        box = self._pending.get(message["id"])
                    if box is not None:
                        box.put(message)
        finally:
            self.closed.set()
            with self._lock:
                for box in self._pending.values():
                    box.put({"error": {"message": "ACP process exited"}})

    def _answer(self, message: dict) -> None:
        try:
            result = self.on_request(message["method"], message.get("params") or {})
            reply = {"jsonrpc": "2.0", "id": message["id"], "result": result}
            if result is None:
                reply = {"jsonrpc": "2.0", "id": message["id"],
                         "error": {"code": -32601, "message": "not supported by Hermes Line"}}
        except Exception:
            reply = {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32603, "message": "client error"}}
        try:
            self._send(reply)
        except AcpError:
            pass

    def close(self, timeout: float = 5.0) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(2)
        self._reader.join(2)
        try:
            if self.proc.stdout:
                self.proc.stdout.close()
        except OSError:
            pass
        self.closed.set()


def _decline_permission(params: dict) -> dict:
    """Pick a reject option if the agent offered one, else report the request as cancelled."""
    for option in params.get("options") or []:
        if str(option.get("kind", "")).startswith("reject"):
            return {"outcome": {"outcome": "selected", "optionId": option.get("optionId")}}
    return {"outcome": {"outcome": "cancelled"}}


class AcpDriver:
    def __init__(self, session_id: str, *, command: Optional[list[str]] = None, env: Optional[dict] = None,
                 cwd: Optional[str] = None, start_timeout_s: float = 60.0, turn_timeout_s: float = 120.0,
                 ignore_rules: bool = False):
        self.session_id = session_id            # the plugin's name for this call session
        self.hermes_session_id: Optional[str] = None  # the gateway session id (in-call tool context)
        self._command = command
        self._env = env
        self._cwd = cwd
        self._own_cwd: Optional[str] = None     # the fresh directory ``_workdir`` made; removed by ``close``
        self._ignore_rules = ignore_rules       # the plugin's call sessions: Hermes's --ignore-rules, set here
        self._start_timeout = start_timeout_s
        self._turn_timeout = turn_timeout_s
        self._conn: Optional[_Connection] = None
        self._ask_session: Optional[str] = None
        self._briefing = ""
        self._prefix: list[str] = []
        self._lock = threading.Lock()
        self._gen = 0
        self._active: Optional[dict] = None     # {"session", "gen", "emit", "chunks"}
        # Round 7: a question (``ask``: detector, summary) runs in its own ACP session and its own slot, so it
        # can overlap a spoken turn: it never replaces the turn's slot or bumps the turn generation.
        self._ask_active: Optional[dict] = None
        self._ask_lock = threading.Lock()
        self.on_refusal: Optional[Callable[[str, str], None]] = None  # round 9: (session kind, reason) for the log
        self._interrupted: set[int] = set()
        self.last_usage: Optional[dict] = None  # the gateway's session counters from the last turn
        self._mcp_servers: list = []            # round 6: the call's session MCP server (bearer header inside)
        self._tool_check: Optional[Callable[[str], Optional[list]]] = None  # round 8: rule 12 guard
        self._ask_check: Optional[Callable[[str], Optional[list]]] = None   # round 9 N6: same for the ask session
        self._ask_mcp_servers: list = []
        self._ask_refused: Optional[str] = None
        self.session_tools_reply: Optional[str] = None
        self.ask_tools_reply: Optional[str] = None

    def restrict_tools(self, check: Callable[[str], Optional[list]], *,
                       ask_check: Optional[Callable[[str], Optional[list]]] = None) -> None:
        """Rounds 8-9: after ``session/new``, read each session's scope (``/tools``) and refuse to use it unless the
        check returns []. None (unreadable) and any problem fail closed. Once restricted, the detector/summary
        session needs its own ``ask_check``; without one, ``ask`` never sends anything."""
        self._tool_check = check
        self._ask_check = ask_check

    def direct_tools_note(self) -> str:
        """Round 12: after the scope check, the exact names of this session's deferred call tools (from Hermes's
        ``Enabled toolsets: mcp-editor_<server>_<tag>``), so the model calls them with ``tool_call`` at once
        instead of a ``tool_search`` round first (live calls: every finish took 5.7-7.3 s that way)."""
        import re as _re
        found = _re.fullmatch(r"Enabled toolsets: (mcp-editor_hl_[0-9a-f]{8}_[0-9a-f]{8})", (self.session_tools_reply
                                                                                          or "").strip())
        if not found:
            return ""
        prefix = "mcp__" + found.group(1)[len("mcp-"):] + "__"
        return ("Your call tools are deferred; call them directly with tool_call, no tool_search or tool_describe "
                f"needed: {prefix}hermes_line_call_finish {{outcome: done|callback_expected|failed, summary}}, "
                f"{prefix}hermes_line_call_press_digits {{digits}}, {prefix}hermes_line_call_bring_owner {{}}.")

    def add_briefing(self, text: str) -> None:
        """Append to the briefing that goes with the first turn (before any turn only)."""
        if self._briefing and text:
            self._briefing = self._briefing + "\n" + text

    def set_ask_mcp_servers(self, servers: list) -> None:
        """Round 9 N6: ACP ``mcpServers`` for the detector/summary session (freezes its toolsets at creation)."""
        self._ask_mcp_servers = [dict(s) for s in servers or []]

    def set_mcp_servers(self, servers: list) -> None:
        """ACP ``mcpServers`` for ``session/new``. They carry a bearer token: never logged or echoed."""
        self._mcp_servers = [dict(s) for s in servers or []]

    # -- lifecycle -----------------------------------------------------------
    def start(self, briefing: str) -> None:
        if self._conn is not None:
            return
        env = child_env(self._env)
        if self._ignore_rules:  # set by the plugin, never inherited: no SOUL.md, context files, memory or skills
            env["HERMES_IGNORE_RULES"] = "1"
        try:
            self._start(briefing, env)
        except BaseException:
            self.close()  # a failed start leaves no child process and no temp dir behind (readiness RC-6)
            raise

    def _start(self, briefing: str, env: dict) -> None:
        conn = _Connection(self._command or default_command(), env, self._workdir())
        conn.on_notification = self._on_notification
        conn.on_request = self._on_request
        self._conn = conn
        conn.request("initialize", {"protocolVersion": PROTOCOL_VERSION,
                                    "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                                           "terminal": False}},
                     timeout=self._start_timeout)
        result = conn.request("session/new", {"cwd": self._workdir(), "mcpServers": self._mcp_servers},
                              timeout=self._start_timeout)
        sid = result.get("sessionId")
        if not isinstance(sid, str) or not sid:
            raise AcpError("session/new returned no session id")
        self.hermes_session_id = sid
        self._briefing = briefing
        if self._tool_check is not None:
            res = self._prompt(sid, "/tools", None, self._start_timeout)  # answered by the viewer, not the model
            self.session_tools_reply = res.text
            extra = self._tool_check(res.text or "") if not res.error else None
            if extra != []:
                what = "unreadable" if extra is None else f"{len(extra)} extra, e.g. {', '.join(extra[:4])}"
                raise AcpError(f"call session not restricted to the call tools ({what})")

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            conn.close()
        own, self._own_cwd = self._own_cwd, None
        if own is not None:
            shutil.rmtree(own, ignore_errors=True)

    def _workdir(self) -> str:
        """The session's working directory. The plugin passes none, so each call gets a fresh, empty, owner-only
        (0700) directory under ``clean_temp_root()`` (no ``.git`` or project marker at or above it), made at
        ``start`` and removed by ``close`` (the ask session shares it). Never the process's own: Hermes loads a
        session cwd's context files (AGENTS.md and the like) and, from a cwd inside a git repository or project, a
        workspace block with its root, branch, status and recent commit subjects, and a stranger partly drives the
        session (Nous review of d92bc1fb; S7 delta review R1). An explicit ``cwd`` (the trial harness's run
        directory) is used as given."""
        if self._cwd:
            return self._cwd
        if self._own_cwd is None:
            self._own_cwd = tempfile.mkdtemp(prefix="hermes-line-call-", dir=clean_temp_root())
        return self._own_cwd

    # -- protocol callbacks (reader thread) --------------------------------------
    def _on_notification(self, method: str, params: dict) -> None:
        if method != "session/update":
            return
        update = params.get("update") or {}
        if update.get("sessionUpdate") != "agent_message_chunk":
            return
        content = update.get("content") or {}
        text = content.get("text") if content.get("type") == "text" else None
        if not isinstance(text, str) or not text:
            return
        with self._lock:
            ask = self._ask_active
            if ask is not None and params.get("sessionId") == ask["session"]:
                ask["chunks"].append(text)
                return
            active = self._active
            if active is None or params.get("sessionId") != active["session"]:
                return
            if active["gen"] != self._gen or active["gen"] in self._interrupted:
                return  # stale generation: never reaches the caller
            active["chunks"].append(text)
            if not active["first"] and text.strip():
                active["first"] = (time.perf_counter() - active["t0"]) * 1000
            emit = active["emit"]
        if emit is not None:
            emit(text)

    def _on_request(self, method: str, params: dict) -> Any:
        if method == "session/request_permission":
            return _decline_permission(params)
        return None  # fs/terminal and anything else: not offered by this client

    # -- turns ------------------------------------------------------------------
    def _prompt(self, session: str, text: str, emit: Optional[Callable[[str], None]], timeout: float) -> TurnResult:
        if self._conn is None:
            raise AcpError("driver not started")
        with self._lock:
            self._gen += 1
            gen = self._gen
            active = {"session": session, "gen": gen, "emit": emit, "chunks": [], "first": None,
                      "t0": time.perf_counter()}
            self._active = active
        error = None
        try:
            result = self._conn.request("session/prompt", {"sessionId": session,
                                                           "prompt": [{"type": "text", "text": text}]},
                                        timeout=timeout)
            stop = result.get("stopReason")
            if isinstance(result.get("usage"), dict):
                self.last_usage = result["usage"]
        except AcpError as exc:
            error, stop = str(exc)[:120], "error"
        with self._lock:
            interrupted = gen in self._interrupted or stop == "cancelled"
            if self._active is active:
                self._active = None
        return TurnResult(text="".join(active["chunks"]), interrupted=interrupted, error=error,
                          first_text_ms=active["first"], full_ms=(time.perf_counter() - active["t0"]) * 1000)

    def turn(self, text: str, emit: Callable[[str], None]) -> TurnResult:
        parts = []
        if self._briefing:
            parts.append(f"[call briefing]\n{self._briefing}")
            self._briefing = ""
        with self._lock:
            parts.extend(self._prefix)
            self._prefix = []
        parts.append(text)
        return self._prompt(self.hermes_session_id or "", "\n\n".join(parts), emit, self._turn_timeout)

    def interrupt(self, spoken_prefix: Optional[str] = None) -> None:
        with self._lock:
            active = self._active
            target = active["gen"] if active else self._gen
            self._interrupted.add(target)
            self._gen += 1
            heard = (spoken_prefix if spoken_prefix is not None
                     else "".join(active["chunks"]) if active else None)
            if heard is not None and heard.strip():  # round 12: nothing heard, nothing to correct
                self._prefix.append(f"[system] You were interrupted. The other party heard only: \"{heard.strip()}\". "
                                    "Do not assume they heard the rest.")
        if active is not None and self._conn is not None:
            try:
                self._conn.notify("session/cancel", {"sessionId": active["session"]})
            except AcpError:
                pass

    def note(self, text: str) -> None:
        with self._lock:
            self._prefix.append(f"[call note] {text}")

    def _ask_text(self, conn: "_Connection", session: str, text: str, timeout: float) -> str:
        """A prompt in a question session, collecting its text (used for the ``/tools`` scope check)."""
        slot = {"session": session, "chunks": []}
        with self._lock:
            self._ask_active = slot
        try:
            conn.request("session/prompt", {"sessionId": session, "prompt": [{"type": "text", "text": text}]},
                         timeout=timeout)
        finally:
            with self._lock:
                if self._ask_active is slot:
                    self._ask_active = None
        return "".join(slot["chunks"])

    def ask(self, prompt: str, timeout_s: float = 20.0) -> str:
        """A stateless question in the driver's second ACP session. Safe to run while a turn streams
        (round 7): its own slot, no effect on the turn's generation or interruption state."""
        conn = self._conn
        if conn is None:
            return ""
        with self._ask_lock:  # one question at a time; turns are not blocked by it
            if self._ask_refused is not None or (self._tool_check is not None and self._ask_check is None):
                return ""  # round 9 N6: an unchecked or refused question session never gets caller text
            deadline = time.monotonic() + max(timeout_s, 0.1)
            try:
                if self._ask_session is None:
                    result = conn.request("session/new", {"cwd": self._workdir(),
                                                          "mcpServers": list(self._ask_mcp_servers)},
                                          timeout=min(self._start_timeout, max(timeout_s, 0.1)))
                    sid = result.get("sessionId")
                    if self._ask_check is not None:
                        reply = self._ask_text(conn, sid or "", "/tools", max(deadline - time.monotonic(), 1.0))
                        self.ask_tools_reply = reply
                        problems = self._ask_check(reply or "")
                        if problems != []:
                            self._ask_refused = ("unreadable" if problems is None
                                                 else f"{len(problems)} problem(s), e.g. {', '.join(problems[:3])}")
                            if self.on_refusal is not None:
                                self.on_refusal("ask", self._ask_refused)
                            return ""
                    self._ask_session = sid
                session = self._ask_session or ""
                slot = {"session": session, "chunks": []}
                with self._lock:
                    self._ask_active = slot
                try:
                    conn.request("session/prompt", {"sessionId": session, "prompt": [{"type": "text", "text":
                                 "Independent question; ignore any earlier ones.\n" + prompt}]},
                                 timeout=max(deadline - time.monotonic(), 0.1))
                finally:
                    with self._lock:
                        if self._ask_active is slot:
                            self._ask_active = None
            except AcpError:
                if self._ask_session:
                    try:
                        conn.notify("session/cancel", {"sessionId": self._ask_session})
                    except AcpError:
                        pass
                return ""
            return "".join(slot["chunks"])
