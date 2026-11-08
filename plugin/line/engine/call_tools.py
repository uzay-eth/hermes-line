"""The in-call tools a call's own Hermes session may use, and the bearer tokens of the session MCP server.

One definition serves both routes:

* in-process: the plugin's registered tools (``hermes_glue``), when the Hermes session runs in a profile
  that shares this service's store (``CallService.in_call``);
* session MCP (round 6): ``server.call_tools`` serves the same tools over MCP on loopback, and
  ``AcpDriver`` hands that server to the gateway in ``session/new`` (``mcpServers``). The tool call then
  reaches this service directly, whatever profile the gateway runs in.

A token is 256 bits from ``secrets``, bound to exactly one call, kept only as its SHA-256 digest, valid
until the call ends (revoked in ``CallService.session_finished``) or its time limit passes. Nothing
here grants more than the in-process handlers do: each tool runs the same ``CallSession`` method, with
its own state checks, destination gate and consent.
"""
from __future__ import annotations

import hashlib
import re
import hmac
import secrets
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple


def _obj(props: dict, required: list) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


IN_CALL_TOOLS: Dict[str, dict] = {
    "hermes_line_call_press_digits": {
        "description": "During a live call only: press keys on the phone menu (0-9, *, #, w = half-second pause).",
        "parameters": _obj({"digits": {"type": "string"}}, ["digits"]),
    },
    "hermes_line_call_finish": {
        "description": "During a live call only: the task is done or cannot be done; end the call after your goodbye.",
        "parameters": _obj({"outcome": {"type": "string", "enum": ["done", "callback_expected", "failed"]},
                            "summary": {"type": "string", "description": "One or two sentences for the owner."}},
                           ["outcome", "summary"]),
    },
    "hermes_line_call_bring_owner": {
        "description": "During a live call only: ring the owner in because the other party needs them personally.",
        "parameters": _obj({}, []),
    },
    # Round 13 (owner line mode). Works only on a call where the owner passed the PIN; anyone else is refused.
    "hermes_line_call_new_task": {
        "description": ("During a live call with the owner only (after their PIN): ask to phone a business for them. "
                        "The line reads the number back and the owner's spoken yes approves that one call; it is "
                        "dialled after this call ends. Set bring_owner when they want to be rung in when a person "
                        "answers."),
        "parameters": _obj({"destination": {"type": "string", "description": "The business number, E.164."},
                            "title": {"type": "string"}, "goal": {"type": "string"},
                            "bring_owner": {"type": "boolean"}}, ["destination", "title", "goal"]),
    },
}

# tool name -> CallSession method (the CallService.IN_CALL set)
METHODS: Dict[str, str] = {
    "hermes_line_call_press_digits": "press_digits",
    "hermes_line_call_finish": "request_finish",
    "hermes_line_call_bring_owner": "request_owner",
    "hermes_line_call_new_task": "owner_new_task",
}

SERVER_NAME = "hermes_line"
BRIEFING_NOTE = ("The in-call tools may be listed with an MCP prefix (for example "
                 "mcp__..._hermes_line_call_finish); they are the same tools.")


def call_args(method: str, args: Any) -> Tuple[str, ...]:
    """The positional arguments of ``method`` from a tool's argument object (same as the in-process tools)."""
    args = args if isinstance(args, dict) else {}
    return {"press_digits": (str(args.get("digits", "")),),
            "request_finish": (str(args.get("outcome", "")), str(args.get("summary", ""))),
            "request_owner": (),
            "owner_new_task": (str(args.get("destination", "")), str(args.get("title", "")),
                               str(args.get("goal", "")), "1" if args.get("bring_owner") is True else "")}[method]


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

# Round 8 (catalog rule 12), corrected in round 9 (R1 delta 4 N4): the call's Hermes session may hold only this
# call's own session MCP server. A stranger on the phone partly drives that session, so it must not reach terminal,
# file, browser, web, memory, delegation, plugin or other MCP tools, shown or deferred.
#
# What can be verified over ACP (Hermes v0.22.5, ``/tools`` = ``session.info``): when the session has no directly
# exposed tools, Hermes prints ``Enabled toolsets: …``, the session's complete frozen toolset list (every MCP
# server, plugin and built-in toolset by name). When tools are exposed it prints only their names, and deferred
# tools collapse into ``tool_search``/``tool_describe``/``tool_call``, so the full scope is NOT visible. The bridge
# names therefore never pass: a reply that shows them is refused.
BRIDGE_TOOLS = frozenset({"tool_search", "tool_describe", "tool_call"})
UNRESTRICTED_HELP = ("the call's Hermes session has tools beyond this call's own tools; set call_profile to a Hermes "
                     "profile whose ACP toolsets are empty (platform_toolsets: {acp: []}) with no MCP servers or "
                     "plugin toolsets")
_SERVER_NAME = re.compile(r"hl_[0-9a-f]{8}")


def new_server_name() -> str:
    """A per-call session MCP server name (round 9): Hermes builds the toolset ``mcp-editor_<name>_<tag>`` from it,
    so only this call's server can match. Not a secret (the bearer token is). Short on purpose: Hermes renames any
    ``mcp__editor_<name>_<tag>__<tool>`` over 64 characters to a hash, which breaks ``tool_search`` by name; with
    ``hl_`` + 8 hex the longest call tool is 63 characters."""
    return f"hl_{secrets.token_hex(4)}"


def _own_toolset(name: str, server_names: list) -> bool:
    return any(re.fullmatch(r"mcp-editor_" + re.escape(server) + r"_[0-9a-f]{8}", name) for server in server_names)


def _own_tool(name: str, server_names: list, tools: frozenset) -> bool:
    return any(re.fullmatch(r"mcp__editor_" + re.escape(server) + r"_[0-9a-f]{8}__(" + "|".join(sorted(tools)) + ")",
                            name) for server in server_names)


def session_tool_names(tools_reply: str) -> Optional[list]:
    """The tool names in an ACP ``/tools`` reply (``Available tools (N): …``), or None if it is not that form."""
    text = (tools_reply or "").strip()
    lines = text.splitlines()
    head = re.fullmatch(r"Available tools \((\d+)\):", lines[0].strip()) if lines else None
    if head is None:
        return None
    names = []
    for line in lines[1:]:
        if not line.strip():
            continue
        group = re.fullmatch(r"\s+[^:]+:\s*(.*)", line)
        if group is None:
            return None
        names += [n.strip() for n in group.group(1).split(",") if n.strip()]
    return names if len(set(names)) == int(head.group(1)) else None


def disallowed_session_tools(tools_reply: str, server_names: list, tools: frozenset = frozenset(METHODS)) -> \
        Optional[list]:
    """[] when the session's complete scope is exactly this call's own MCP server(s) ``server_names`` (with
    ``tools`` as their only tools); the problems otherwise; None when the reply cannot be read. Every caller
    treats anything but [] as refusal (fail closed).

    Accepted: ``Enabled toolsets: X[, Y]`` where the set is exactly one ``mcp-editor_<server>_<tag>`` per expected
    server; or ``Available tools (N): …`` whose every name is one of this call's own tools and which shows no
    bridge (then nothing is deferred and the list is the scope)."""
    text = (tools_reply or "").strip()
    servers = [s for s in server_names if _SERVER_NAME.fullmatch(s or "")]
    if not servers or len(servers) != len(server_names):
        return ["no session MCP identity to bind"]
    listed = re.fullmatch(r"Enabled toolsets: (.+)", text)
    if listed:
        toolsets = [t.strip() for t in listed.group(1).split(",") if t.strip()]
        if not toolsets:
            return None
        problems = sorted(t for t in toolsets if not _own_toolset(t, servers))
        problems += [f"missing session MCP server {s}" for s in servers
                     if sum(1 for t in toolsets if _own_toolset(t, [s])) != 1]
        return problems
    if text == "No tools available.":
        return [f"missing session MCP server {s}" for s in servers]
    names = session_tool_names(text)
    if names is None:
        return None
    problems = sorted(n for n in names if n not in BRIDGE_TOOLS and not _own_tool(n, servers, tools))
    if any(n in BRIDGE_TOOLS for n in names):
        problems.append("deferred tools behind tool_search/tool_call cannot be verified")
    return problems


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def require_loopback(host: str) -> str:
    """The session MCP server is never reachable from outside this machine."""
    if host not in LOOPBACK_HOSTS:
        raise ValueError("the call tool server binds to loopback only")
    return host


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class CallToolTokens:
    """Per-call bearer tokens for the session MCP server (thread-safe)."""

    def __init__(self, ttl_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl_s = float(ttl_s)
        self._clock = clock
        self._lock = threading.Lock()
        self._by_digest: Dict[str, Tuple[str, float, str]] = {}  # digest -> (call_id, expires_at, role)

    def issue(self, call_id: str, role: str = "call") -> str:
        """A fresh token for ``call_id`` and ``role`` ("call": the call tools; "ask": the detector/summary session's
        empty server, round 9 N6); any earlier token of that call and role stops working."""
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._drop(call_id, role)
            self._by_digest[_digest(token)] = (call_id, self._clock() + self.ttl_s, role)
        return token

    def resolve(self, token: Optional[str]) -> Optional[str]:
        """The call of a ``call`` token (an ``ask`` token reaches no call tool)."""
        found = self.resolve_role(token)
        return found[0] if found is not None and found[1] == "call" else None

    def resolve_role(self, token: Optional[str]) -> Optional[Tuple[str, str]]:
        if not isinstance(token, str) or not token or len(token) > 128:
            return None
        digest = _digest(token)
        now = self._clock()
        with self._lock:
            for held, (call_id, expires_at, role) in list(self._by_digest.items()):
                if expires_at <= now:
                    del self._by_digest[held]
            entry = None
            for held, value in self._by_digest.items():
                if hmac.compare_digest(held, digest):
                    entry = value
            return (entry[0], entry[2]) if entry is not None else None

    def revoke(self, call_id: str) -> None:
        with self._lock:
            self._drop(call_id)

    def active(self) -> int:
        with self._lock:
            return len(self._by_digest)

    def _drop(self, call_id: str, role: Optional[str] = None) -> None:
        for held, (owner, _, kind) in list(self._by_digest.items()):
            if owner == call_id and (role is None or kind == role):
                del self._by_digest[held]
