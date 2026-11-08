"""Run the ingress app on uvicorn in a dedicated thread with its own event loop.

``register(ctx)`` must return quickly and may run without an event loop, so the
service never borrows the host's loop. The socket is bound before the thread
starts (port 0 gives an ephemeral port for tests). Stop is bounded.

A second listener on the same loop serves the call tools' session MCP server (round 6). It binds to
loopback only, on its own ephemeral port, never on the (tunnelled) ingress port.
"""
from __future__ import annotations

import asyncio
import socket
import threading
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover
    from ..engine.service import CallService


class ServerThread:
    def __init__(self, svc: "CallService", *, host: str, port: int, tools_host: Optional[str] = "127.0.0.1"):
        from ..engine.call_tools import require_loopback
        self.svc = svc
        self.host = host
        self.port = port
        self.tools_host = require_loopback(tools_host) if tools_host is not None else None
        self.tools_port: Optional[int] = None
        self._server = None
        self._tools_server = None
        self._thread: Optional[threading.Thread] = None
        self._error: Optional[BaseException] = None

    def start(self, ready_timeout: float = 10.0) -> None:
        import uvicorn

        from .ingress import build_app

        sock = socket.socket(socket.AF_INET6 if ":" in self.host else socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(64)
        self.port = sock.getsockname()[1]
        try:  # uvicorn >= 0.35: the maintained sans-I/O websockets protocol
            import uvicorn.protocols.websockets.websockets_sansio_impl  # noqa: F401
            ws_impl = "websockets-sansio"
        except ImportError:
            ws_impl = "auto"
        config = uvicorn.Config(build_app(self.svc), log_level="warning", access_log=False, lifespan="off", ws=ws_impl,
                                ws_max_size=64 * 1024, proxy_headers=False, server_header=False,
                                timeout_graceful_shutdown=3, ws_ping_interval=20, ws_ping_timeout=20)
        self._server = uvicorn.Server(config)
        tools_sock = None
        if self.tools_host is not None:
            from .call_tools import build_mcp_app
            tools_sock = socket.socket(socket.AF_INET6 if ":" in self.tools_host else socket.AF_INET,
                                       socket.SOCK_STREAM)
            tools_sock.bind((self.tools_host, 0))
            tools_sock.listen(16)
            self.tools_port = tools_sock.getsockname()[1]
            hosts = {f"{name}:{self.tools_port}" for name in ("127.0.0.1", "localhost", "[::1]")}
            self._tools_server = uvicorn.Server(uvicorn.Config(
                build_mcp_app(self.svc, hosts), log_level="warning", access_log=False, lifespan="off", ws="none",
                proxy_headers=False, server_header=False, timeout_graceful_shutdown=2))

        async def main() -> None:
            await self.svc._on_loop_start()
            if self._tools_server is not None:
                await asyncio.gather(self._server.serve(sockets=[sock]), self._tools_server.serve(sockets=[tools_sock]))
            else:
                await self._server.serve(sockets=[sock])

        def run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(main())
            except BaseException as exc:  # surfaced to start()/stop()
                self._error = exc
            finally:
                try:
                    pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                    for task in pending:
                        task.cancel()
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                finally:
                    loop.close()
                    sock.close()
                    if tools_sock is not None:
                        tools_sock.close()

        self._thread = threading.Thread(target=run, name="hermes-line-service", daemon=True)
        self._thread.start()
        deadline = time.monotonic() + ready_timeout
        while time.monotonic() < deadline:
            if self._server.started and (self._tools_server is None or self._tools_server.started):
                return
            if self._error is not None or not self._thread.is_alive():
                raise RuntimeError(f"ingress failed to start: {type(self._error).__name__}")
            time.sleep(0.01)
        self.stop(timeout=2)
        raise TimeoutError("ingress did not start in time")

    def stop(self, timeout: float = 10.0) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._tools_server is not None:
            self._tools_server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout)
            if self._thread.is_alive():
                raise TimeoutError("ingress thread did not stop")
            self._thread = None
