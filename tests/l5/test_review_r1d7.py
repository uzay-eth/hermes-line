"""R1 delta 7 regressions (evidence/reviews/R1-DELTA7-voice.md).

N9: a cancelled or failed Grok/STT setup closes its socket; ``VoiceCall.close`` cancels setups
in flight, waits for their cleanup, and leaves zero open sockets.
N10: no Authorization header (the xAI key) reaches any log record at any level, or any
exception text, on success or on a failed handshake. Uses the real ``websockets`` client
against a loopback server with a synthetic key; no network beyond 127.0.0.1.
"""
from __future__ import annotations

import asyncio
import io
import logging
import traceback
import unittest

from helpers import Rig, run

from plugin.line.voice.grok import GrokRealtimeProvider
from plugin.line.voice.provider import ProviderUnavailable, SessionConfig, transport_logger, websockets_connector
from plugin.line.voice.stt import XaiStreamingTranscriber

KEY = "xai-SYNTHETIC-fixture-key-0123456789abcdef"


class Wire:
    """A fake provider socket: the handshake has succeeded; ``recv`` waits forever."""

    registry: list = []

    def __init__(self, *, fail_send: bool = False) -> None:
        self.closed = False
        self.fail_send = fail_send
        self.waiting = asyncio.Event()
        Wire.registry.append(self)

    async def send(self, data):
        if self.fail_send:
            raise ConnectionResetError("send failed")

    async def recv(self):
        self.waiting.set()
        await asyncio.Future()

    async def close(self):
        self.closed = True


def open_wires() -> int:
    return sum(1 for w in Wire.registry if not w.closed)


def connector_for(wire: Wire):
    async def connect(url, headers):
        return wire
    return connect


class N9CancellationTests(unittest.TestCase):
    def setUp(self):
        Wire.registry = []

    def test_reviewer_probe_cancelled_setup_socket_is_closed(self):
        """fixtures/probe_voice.py from the review, with the assertion inverted."""
        async def go():
            w = Wire()
            rig = Rig()
            rig.voice._provider_factory = lambda: GrokRealtimeProvider(lambda n: KEY, connector=connector_for(w))
            task = asyncio.create_task(rig.voice._open("conversation", "fixture"))
            await asyncio.wait_for(w.waiting.wait(), 2)      # handshake done, waiting for session.updated
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(w.closed)                        # closed by the setup itself
            await rig.voice.close()
            self.assertIsNone(rig.voice.provider)
            self.assertEqual(rig.voice._unadopted, [])
            self.assertEqual(open_wires(), 0)
        run(go())

    def test_close_cancels_a_setup_in_flight_and_waits_for_it(self):
        async def go():
            w = Wire()
            rig = Rig()
            rig.voice._provider_factory = lambda: GrokRealtimeProvider(lambda n: KEY, connector=connector_for(w))
            await rig.start("agent_talking")               # the gate opens a session; readiness never comes
            await asyncio.wait_for(w.waiting.wait(), 2)
            self.assertEqual(open_wires(), 1)
            await rig.voice.close()                        # must not return before the socket is closed
            self.assertTrue(w.closed)
            await asyncio.sleep(0.01)
            # only the setup was cancelled, never the task hosting it (the Twilio stream reader)
            self.assertFalse(rig.serve_task.cancelled())
            self.assertEqual(rig.voice.counters["event_errors"], 0)
            self.assertEqual(open_wires(), 0)
            self.assertIsNone(rig.voice.provider)
            self.assertEqual(rig.voice.meter.seconds(), 0.0)
            await rig.close()
        run(go())

    def test_initial_session_update_failure_closes_the_socket(self):
        async def go():
            w = Wire(fail_send=True)
            p = GrokRealtimeProvider(lambda n: KEY, connector=connector_for(w))
            with self.assertRaises(ProviderUnavailable):
                await p.connect(SessionConfig())
            self.assertTrue(w.closed)
        run(go())

    def test_handshake_that_completes_after_cancellation_is_closed(self):
        async def go():
            created = []
            release = asyncio.Event()

            async def stubborn(url, headers):   # finishes its handshake even though it was cancelled
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    pass
                w = Wire()
                created.append(w)
                return w
            for make in (lambda: GrokRealtimeProvider(lambda n: KEY, connector=stubborn).connect(SessionConfig()),
                         lambda: XaiStreamingTranscriber(lambda n: KEY, connector=stubborn).start(None)):
                task = asyncio.create_task(make())
                await asyncio.sleep(0.01)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                await asyncio.sleep(0.01)
            self.assertEqual(len(created), 2)
            self.assertTrue(all(w.closed for w in created))
        run(go())

    def test_stt_cancelled_during_handshake_or_closed_after_leaves_no_socket(self):
        async def go():
            # 1) cancelled while the handshake is still running: nothing is created, nothing leaks
            gate = asyncio.Event()

            async def slow(url, headers):
                await gate.wait()
                return Wire()
            t = XaiStreamingTranscriber(lambda n: KEY, connector=slow)
            task = asyncio.create_task(t.start(None))
            await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(t.running)
            self.assertEqual(open_wires(), 0)

            # 2) VoiceCall.close while the STT handshake is in flight: cancelled and waited for
            Wire.registry = []
            pending = asyncio.Event()
            proceed = asyncio.Event()

            async def held(url, headers):
                pending.set()
                await proceed.wait()
                return Wire()
            rig = Rig()
            rig.voice._transcriber_factory = lambda: XaiStreamingTranscriber(lambda n: KEY, connector=held)
            await rig.voice.sync_state("on_hold")
            serve = asyncio.create_task(rig.voice.serve(rig.client.socket))
            rig.client.start()
            await asyncio.wait_for(pending.wait(), 2)
            await rig.voice.close()
            proceed.set()
            await asyncio.sleep(0.02)
            self.assertIsNone(rig.voice.transcriber)
            self.assertEqual(open_wires(), 0)

            # 3) adopted STT stream: VoiceCall.close closes it
            Wire.registry = []
            w = Wire()
            rig2 = Rig()
            rig2.voice._transcriber_factory = lambda: XaiStreamingTranscriber(lambda n: KEY, connector=connector_for(w))
            await rig2.start("on_hold")
            self.assertTrue(rig2.voice.transcriber.running)
            await rig2.voice.close()
            self.assertTrue(w.closed)
            self.assertEqual(open_wires(), 0)
            rig.client.hang_up()
            await asyncio.wait([serve], timeout=1)
            await rig2.close()
        run(go())


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.texts = []
        self.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))

    def emit(self, record):
        self.texts.append(self.format(record))


class N10LoggingTests(unittest.TestCase):
    def setUp(self):
        self.root = logging.getLogger()
        self.cap = Capture()
        self.saved = (self.root.level, logging.getLogger("websockets").level)
        self.root.addHandler(self.cap)
        self.root.setLevel(logging.DEBUG)                     # an operator debugging the first call
        logging.getLogger("websockets").setLevel(logging.DEBUG)

    def tearDown(self):
        self.root.removeHandler(self.cap)
        self.root.setLevel(self.saved[0])
        logging.getLogger("websockets").setLevel(self.saved[1])

    def assertNoKey(self, *extra):
        blob = "\n".join(self.cap.texts) + "\n" + "\n".join(str(x) for x in extra)
        self.assertNotIn(KEY, blob)
        self.assertNotIn("Bearer " + KEY, blob)

    async def server(self, *, reject: bool = False):
        from websockets.asyncio.server import serve
        quiet = logging.getLogger("l5.test.server")       # the TEST server's own logs are not the plugin's
        quiet.setLevel(logging.CRITICAL)
        quiet.propagate = False
        seen = []

        def process_request(connection, request):
            seen.append(request.headers.get("Authorization"))
            if reject:
                return connection.respond(401, "invalid key\n")
            return None

        async def handler(ws):
            async for msg in ws:
                if '"session.update"' in msg:
                    await ws.send('{"type":"session.updated","session":{}}')

        srv = await serve(handler, "127.0.0.1", 0, process_request=process_request, logger=quiet)
        port = srv.sockets[0].getsockname()[1]
        return srv, f"ws://127.0.0.1:{port}", seen

    def test_successful_grok_and_stt_handshakes_log_no_key(self):
        async def go():
            srv, base, seen = await self.server()
            try:
                p = GrokRealtimeProvider(lambda n: KEY, url=base + "/v1/realtime")
                await p.connect(SessionConfig())
                self.assertTrue(p.connected)
                await p.close()
                t = XaiStreamingTranscriber(lambda n: KEY, url=base + "/v1/stt")
                await t.start(lambda text: asyncio.sleep(0))
                await t.feed("AAAA")
                await t.stop()
            finally:
                srv.close()
                await srv.wait_closed()
            self.assertEqual(seen, ["Bearer " + KEY, "Bearer " + KEY])  # the key really was sent
            self.assertTrue(self.cap.texts, "DEBUG capture saw nothing at all")
            self.assertNoKey()
        run(go())

    def test_failed_handshake_logs_and_exceptions_carry_no_key(self):
        async def go():
            srv, base, seen = await self.server(reject=True)
            errors = []
            try:
                for make in (lambda: GrokRealtimeProvider(lambda n: KEY, url=base + "/v1/realtime").connect(
                                 SessionConfig()),
                             lambda: XaiStreamingTranscriber(lambda n: KEY, url=base + "/v1/stt").start(None)):
                    try:
                        await make()
                    except ProviderUnavailable as exc:
                        errors.append(exc)
                # the raw transport error too (what a caller of the connector would see)
                try:
                    await websockets_connector(base + "/x", {"Authorization": "Bearer " + KEY})
                except Exception as exc:
                    raw = exc
            finally:
                srv.close()
                await srv.wait_closed()
            self.assertEqual(len(errors), 2)
            for exc in errors:
                self.assertIsNone(exc.__cause__)
                self.assertTrue(exc.__suppress_context__)
            texts = [repr(e) for e in errors] + ["".join(traceback.format_exception(e)) for e in errors]
            texts.append("".join(traceback.format_exception(raw)))
            self.assertNoKey(*texts)
        run(go())

    def test_transport_logger_redacts_even_when_set_to_debug_by_hand(self):
        from websockets.client import ClientProtocol
        from websockets.uri import parse_uri
        log = transport_logger()
        saved = log.level
        self.assertGreaterEqual(log.level, logging.INFO)     # default: wire DEBUG logging is off
        try:
            log.setLevel(logging.DEBUG)
            out = io.StringIO()
            h = logging.StreamHandler(out)
            log.addHandler(h)
            proto = ClientProtocol(parse_uri("wss://api.x.ai/v1/realtime"), logger=log)
            req = proto.connect()
            req.headers["Authorization"] = "Bearer " + KEY
            proto.send_request(req)
            log.removeHandler(h)
            self.assertIn("redacted", out.getvalue())
            self.assertNotIn(KEY, out.getvalue())
        finally:
            log.setLevel(saved)
        self.assertNoKey()


if __name__ == "__main__":
    unittest.main()
