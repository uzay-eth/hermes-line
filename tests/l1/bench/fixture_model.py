"""Deterministic OpenAI-compatible streaming model on 127.0.0.1 (benchmark fixture only).

``POST /v1/chat/completions`` streams a short reply as SSE: FIRST_DELAY before the
first chunk, CHUNK_DELAY between chunks. ``GET /v1/models`` lists one model. Used to
compare driver overhead (embedded vs ACP) with identical model timing, and to
rehearse sandboxed runs. Its text is fixture content, not a product claim.

Round 6: when the latest user message contains ``FINISHNOW``, the model calls the
session MCP server's ``hermes_line_call_finish`` (``outcome=done``): directly if it is offered by
name, else through Hermes's deferred route (``tool_search`` then ``tool_call`` with the exact
``mcp__...`` name the search returned), then says a short goodbye. The tool names it was offered are recorded (names only) in ``server.tool_names``.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "l1-fixture"
FIRST_DELAY = 0.020
CHUNK_DELAY = 0.005
WORDS = ["Thank ", "you, ", "that ", "works ", "for ", "us."]
TRIGGER = "FINISHNOW"
FINISH_SUFFIX = "hermes_line_call_finish"


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def _plan(body: dict, server) -> tuple:
    """(tool name, arguments) to call now, or ("", words) to speak.

    The finish tool directly when it is offered by name; otherwise Hermes's deferred route:
    ``tool_search`` for it, then ``tool_call`` with the exact name the search returned."""
    import re
    names = [t.get("function", {}).get("name", "") for t in body.get("tools") or [] if isinstance(t, dict)]
    server.tool_names.append(names)
    messages = body.get("messages") or []
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    if last_user < 0 or TRIGGER not in _text(messages[last_user].get("content")):
        return "", WORDS
    after = messages[last_user + 1:]
    called = [c.get("function", {}).get("name") for m in after if m.get("role") == "assistant"
              for c in m.get("tool_calls") or []]
    results = [_text(m.get("content")) for m in after if m.get("role") == "tool"]
    server.tool_results.extend(r[:300] for r in results[len(server.tool_results_seen):])
    server.tool_results_seen = results
    args = {"outcome": "done", "summary": "Table for four booked for Friday at 7:30 pm."}
    finish = next((n for n in names if n.endswith(FINISH_SUFFIX)), None)
    if finish and finish not in called:
        return finish, args
    # Round 12: when the prompt already names the exact deferred tool, call it through tool_call at once
    named = next((m.group(0) for msg in messages for m in [re.search(r"mcp__[A-Za-z0-9_]+__" + FINISH_SUFFIX,
                                                                      _text(msg.get("content")))] if m), None)
    if named and "tool_call" in names and "tool_call" not in called and "tool_search" not in called:
        return "tool_call", {"calls": [{"name": named, "arguments": args}]}
    if finish or "tool_call" in called:
        return "", ["Thanks, ", "goodbye."]
    if "tool_search" in names and "tool_search" not in called:
        return "tool_search", {"queries": [FINISH_SUFFIX]}
    found = next((m.group(0) for r in results for m in [re.search(r"mcp__[A-Za-z0-9_]+__" + FINISH_SUFFIX, r)]
                  if m), None)
    if found and "tool_call" in names:
        return "tool_call", {"calls": [{"name": found, "arguments": args}]}
    return "", ["I could not find the finish tool."]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            return self._json(200, {"object": "list", "data": [{"id": MODEL, "object": "model",
                                                                 "context_length": 131072}]})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        size = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(size) or b"{}")
        self.server.requests.append(body)
        if not body.get("stream"):
            return self._json(200, {"id": "f", "object": "chat.completion", "created": 0, "model": MODEL,
                                    "choices": [{"index": 0, "finish_reason": "stop",
                                                 "message": {"role": "assistant", "content": "".join(WORDS)}}],
                                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        def chunk(delta, finish=None):
            data = {"id": "f", "object": "chat.completion.chunk", "created": 0, "model": MODEL,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            self.wfile.write(("data: " + json.dumps(data) + "\n\n").encode())
            self.wfile.flush()

        time.sleep(FIRST_DELAY)
        chunk({"role": "assistant"})
        tool, plan = _plan(body, self.server)
        if tool:
            self.server.calls += 1
            self.server.planned.append(plan["calls"][0]["name"] if tool == "tool_call" else tool)
            chunk({"tool_calls": [{"index": 0, "id": f"call_l1_{self.server.calls}", "type": "function",
                                   "function": {"name": tool, "arguments": json.dumps(plan)}}]})
            chunk({}, "tool_calls")
        else:
            for word in plan:
                chunk({"content": word})
                time.sleep(CHUNK_DELAY)
            chunk({}, "stop")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True


def start(port: int = 0) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    server.daemon_threads = True
    server.requests = []
    server.tool_names = []
    server.tool_results = []
    server.tool_results_seen = []
    server.calls = 0
    server.planned = []  # tool calls the model made, in order (names only)
    threading.Thread(target=server.serve_forever, daemon=True, name="l1-fixture-model").start()
    return server


if __name__ == "__main__":
    import sys
    srv = start(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
    print(srv.server_address[1], flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
