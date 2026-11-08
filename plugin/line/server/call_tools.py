"""Session MCP server for a call's in-call tools (round 6), on loopback only.

MCP Streamable HTTP with JSON responses at ``POST /mcp``: ``initialize``, ``ping``, ``tools/list``,
``tools/call``; notifications get 202; GET/HEAD/DELETE get 405 (no server-initiated stream).
Every request needs ``Authorization: Bearer <token>`` of a live call (``engine.call_tools``), and the
Host header must name this loopback listener (no DNS rebinding). A tool call runs the same
``CallSession`` method as the in-process tool, on the service loop.
"""
from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, Optional

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..engine.call_tools import IN_CALL_TOOLS, METHODS, SERVER_NAME, call_args

ASK = "ask"  # round 9 N6: the detector/summary session's empty server

if TYPE_CHECKING:  # pragma: no cover
    from ..engine.service import CallService

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_BODY = 64 * 1024
BODY_DEADLINE_S = 5.0


class _TooLarge(Exception):
    pass


async def _read_body(request: Request) -> bytes:
    """The body, streamed with a hard cap (R1 delta 3: never buffer more than MAX_BODY) and a deadline."""
    async def read() -> bytes:
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BODY:
                raise _TooLarge()
            chunks.append(chunk)
        return b"".join(chunks)
    return await asyncio.wait_for(read(), BODY_DEADLINE_S)


def tool_list() -> list:
    return [{"name": name, "description": spec["description"], "inputSchema": spec["parameters"]}
            for name, spec in IN_CALL_TOOLS.items()]


def _bearer(header: Optional[str]) -> Optional[str]:
    if not header or not header.startswith("Bearer "):
        return None
    return header[len("Bearer "):].strip() or None


def build_mcp_app(svc: "CallService", allowed_hosts: "set[str]") -> Starlette:
    def reject(reason: str, status: int) -> Response:
        svc.store.log_event(None, None, "call_tools_rejected", {"reason": reason})
        return JSONResponse({"error": reason}, status_code=status)

    def rpc(rid: Any, result: Any = None, error: Optional[dict] = None) -> JSONResponse:
        body = {"jsonrpc": "2.0", "id": rid}
        body.update({"error": error} if error is not None else {"result": result})
        return JSONResponse(body)

    async def mcp(request: Request) -> Response:
        if request.headers.get("host", "") not in allowed_hosts:
            return reject("host", 421)
        if request.method != "POST":
            return Response(status_code=405, headers={"Allow": "POST"})
        token = _bearer(request.headers.get("authorization"))
        found = svc.call_tools.resolve_role(token)
        call_id, role = found if found is not None else (None, None)
        session = svc.sessions.get(call_id) if call_id else None
        if session is None or session.terminal:
            return reject("token", 401)
        try:
            raw = await _read_body(request)
        except _TooLarge:
            return reject("size", 413)
        except Exception:  # deadline passed or the client went away
            return reject("body", 408)

        def still_valid() -> bool:
            """R1 delta 3 N3: the bearer is checked again after every await, so a request authenticated before
            a revocation, re-issue or expiry cannot act after it."""
            return (svc.call_tools.resolve_role(token) == (call_id, role) and svc.sessions.get(call_id) is session
                    and not session.terminal)

        if not still_valid():
            return reject("token", 401)
        try:
            msg = json.loads(raw)
        except ValueError:
            return rpc(None, error={"code": -32700, "message": "parse error"})
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            return rpc(None, error={"code": -32600, "message": "invalid request"})
        if "id" not in msg:
            return Response(status_code=202)  # notifications (initialized, cancelled): nothing to answer
        rid, method, params = msg["id"], msg["method"], msg.get("params") or {}
        if method == "initialize":
            asked = params.get("protocolVersion") if isinstance(params, dict) else None
            version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            return rpc(rid, {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                             "serverInfo": {"name": SERVER_NAME, "version": "1"}})
        if method == "ping":
            return rpc(rid, {})
        if method == "tools/list":
            return rpc(rid, {"tools": [] if role == ASK else tool_list()})
        if method == "tools/call":
            name = params.get("name") if isinstance(params, dict) else None
            if role == ASK or name not in METHODS:
                return rpc(rid, error={"code": -32602, "message": "unknown tool"})
            if not still_valid():  # right before the tool runs; no await in between
                return reject("token", 401)
            out = await svc.call_tool(session, METHODS[name], call_args(METHODS[name], params.get("arguments")))
            return rpc(rid, {"content": [{"type": "text", "text": json.dumps(out)}],
                             "isError": not bool(out.get("ok"))})
        return rpc(rid, error={"code": -32601, "message": "method not found"})

    return Starlette(routes=[Route("/mcp", mcp, methods=["GET", "POST", "DELETE", "HEAD"])])
