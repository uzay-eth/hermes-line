"""A scripted ACP agent on stdio for offline driver tests (newline-delimited JSON-RPC 2.0).

Usage: fake_acp_agent.py LOGFILE [CHUNK_DELAY_S] [TOOLS]
- initialize, session/new (ids sess-1, sess-2, ...), session/prompt streaming "agent_message_chunk"
  updates for the words of REPLY, honouring session/cancel (stopReason "cancelled").
- A prompt containing PERMISSION first asks the client session/request_permission and streams the
  chosen outcome. A prompt containing DIE exits the process mid-turn.
- Every received prompt text is appended to LOGFILE as one JSON line; session/new logs its MCP servers
  (name, type, url and header NAMES only, never a header value).
- The prompt ``/tools`` gets a Hermes-style tool list (``session.info``): TOOLS ``restricted`` (default) lists the
  bridge tools plus the session MCP server's call tools; ``full`` adds a profile's usual tools (terminal, ...);
  ``garbled`` answers something unreadable.
- A prompt containing MCPFINISH plays the gateway's MCP client against the session's first HTTP MCP
  server: initialize, tools/list, then tools/call hermes_line_call_finish, and streams the result.
"""
import json
import sys
import threading
import time

LOG = sys.argv[1]
DELAY = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
TOOLS = sys.argv[3] if len(sys.argv) > 3 else "restricted"
REPLY = ["Hello ", "from ", "the ", "fake ", "agent."]
out_lock = threading.Lock()
cancelled = set()
pending = {}
sessions = [0]
servers = {}


def mcp_post(spec, body):
    import urllib.request
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    headers.update({h["name"]: h["value"] for h in spec.get("headers", [])})
    req = urllib.request.Request(spec["url"], data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        return exc.code, None


def mcp_finish(session):
    spec = next((s for s in servers.get(session, []) if s.get("type") == "http"), None)
    if spec is None:
        return "no-mcp"
    status, init = mcp_post(spec, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "fake", "version": "1"}}})
    if status != 200:
        return f"init-{status}"
    mcp_post(spec, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    status, listed = mcp_post(spec, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [t["name"] for t in listed["result"]["tools"]]
    if "hermes_line_call_finish" not in names:
        return "no-tool"
    status, called = mcp_post(spec, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "hermes_line_call_finish", "arguments": {"outcome": "done", "summary": "Booked for Friday."}}})
    return called["result"]["content"][0]["text"] if status == 200 else f"call-{status}"


def send(msg):
    with out_lock:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()


def chunk(session, text):
    send({"jsonrpc": "2.0", "method": "session/update", "params": {
        "sessionId": session, "update": {"sessionUpdate": "agent_message_chunk",
                                         "content": {"type": "text", "text": text}}}})


def tools_reply(session):
    """Hermes v0.22.5's ``/tools`` forms. ``restricted``: only the session MCP servers, all deferred, so the complete
    toolset list is shown (``Enabled toolsets: mcp-editor_<name>_<tag>``). ``full``: a default profile (names shown,
    bridge present). ``bridge_hidden``: R1 delta 4 N4, an unrelated tool deferred behind the bridge, so only the three
    bridge names show. ``extra_toolset``: an unrelated MCP toolset next to ours. ``drift``: the first session is
    restricted, later ones (detector/summary) get a full profile. ``garbled``: unreadable."""
    mode = TOOLS
    if mode == "drift":
        mode = "restricted" if session == "sess-1" else "full"
    if mode == "garbled":
        return "Tools: lots of them"
    own = [f"mcp-editor_{s.get('name')}_0123abcd" for s in servers.get(session, [])]
    if mode == "restricted":
        return f"Enabled toolsets: {', '.join(own)}\n" if own else "No tools available.\n"
    if mode == "extra_toolset":
        return f"Enabled toolsets: {', '.join(own + ['mcp-other'])}\n"
    if mode == "bridge_hidden":
        return "Available tools (3):\n  other: tool_call, tool_describe, tool_search\n"
    groups = {"other": ["tool_call", "tool_describe", "tool_search"], "terminal": ["terminal"],
              "file": ["patch", "read_file", "write_file"], "web": ["web_search"]}
    names = [n for group in groups.values() for n in group]
    return f"Available tools ({len(names)}):\n" + "".join(f"  {g}: {', '.join(t)}\n" for g, t in sorted(groups.items()))


def prompt(rid, params):
    session = params["sessionId"]
    text = "".join(block.get("text", "") for block in params.get("prompt", []))
    if text.strip() == "/tools":  # answered by the viewer itself in Hermes, never logged as a model prompt
        chunk(session, tools_reply(session))
        send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
        return
    with open(LOG, "a") as fh:
        fh.write(json.dumps({"session": session, "text": text}) + "\n")
    cancelled.discard(session)
    if "DIE" in text:
        chunk(session, "bye")
        sys.stdout.flush()
        import os
        os._exit(3)
    send({"jsonrpc": "2.0", "method": "session/update", "params": {
        "sessionId": session, "update": {"sessionUpdate": "agent_thought_chunk",
                                         "content": {"type": "text", "text": "thinking..."}}}})
    if "MCPFINISH" in text:
        chunk(session, "finish:" + mcp_finish(session) + " ")
    if "PERMISSION" in text:
        box = threading.Event()
        pending[9001] = {"event": box}
        send({"jsonrpc": "2.0", "id": 9001, "method": "session/request_permission", "params": {
            "sessionId": session, "toolCall": {"toolCallId": "t1", "title": "rm -rf /"},
            "options": [{"optionId": "allow_once", "kind": "allow_once", "name": "Allow"},
                        {"optionId": "deny", "kind": "reject_once", "name": "Deny"}]}})
        box.wait(5)
        outcome = (pending[9001].get("result") or {}).get("outcome", {})
        chunk(session, f"permission:{outcome.get('optionId') or outcome.get('outcome')} ")
    for word in REPLY:
        if session in cancelled:
            send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "cancelled"}})
            return
        chunk(session, word)
        time.sleep(DELAY)
    send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})


for line in sys.stdin:
    msg = json.loads(line)
    method, rid = msg.get("method"), msg.get("id")
    if method is None and rid in pending:
        pending[rid]["result"] = msg.get("result")
        pending[rid]["event"].set()
    elif method == "initialize":
        send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": 1, "agentCapabilities": {}}})
    elif method == "session/new":
        sessions[0] += 1
        sid = f"sess-{sessions[0]}"
        servers[sid] = list(msg["params"].get("mcpServers") or [])
        with open(LOG, "a") as fh:
            fh.write(json.dumps({"session_new": [{"name": s.get("name"), "type": s.get("type"), "url": s.get("url"),
                                                  "headers": [h["name"] for h in s.get("headers", [])]}
                                                 for s in servers[sid]]}) + "\n")
        send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": sid}})
    elif method == "session/prompt":
        threading.Thread(target=prompt, args=(rid, msg["params"]), daemon=True).start()
    elif method == "session/cancel":
        cancelled.add(msg["params"]["sessionId"])
    elif rid is not None:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "method not found"}})
