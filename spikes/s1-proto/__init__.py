"""Offline probe. Loading registers surfaces; explicit config enables the listener."""
import atexit
import json
from pathlib import Path
from .server import RelayServer


def register(ctx):
    server = RelayServer(port=int(ctx.get_config("port", 0)))
    ctx.on_unload(server.stop)
    atexit.register(server.stop)
    ctx.register_tool(
        name="hermes_line_noop", toolset="hermes_line_s1",
        schema={"name": "hermes_line_noop", "description": "Return offline probe status; no calls or messages.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
        handler=lambda args, **kwargs: json.dumps({"ok": True, "offline": True, "port": server.port}),
    )
    ctx.register_skill("line-spike", Path(__file__).parent / "skills/line-spike/SKILL.md",
                       description="Explain the offline Hermes Line architecture probe.")
    if ctx.get_config("start_server", False):
        server.start()
