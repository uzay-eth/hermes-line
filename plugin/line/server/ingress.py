"""Authenticated Twilio ingress: voice webhooks and the ConversationRelay WebSocket.

Every request is checked before anything is parsed into the engine (ingress_auth.py):
- The per-install path secret in the URL (``/t/<secret>/twilio/...``), constant-time;
  a wrong one gets a bare 404.
- Signature mode: ``X-Twilio-Signature`` over the URL rebuilt from the configured public
  origin (never Host or forwarded headers) with the account Auth Token.
- Fallback mode (no Auth Token): before a leg's first side effect, Twilio's REST record
  of the CallSid (API key) must show this account, the expected direction and numbers;
  terminal status and conference-end events are re-confirmed against REST.
- ``AccountSid`` must be this account's; ``CallSid`` must be the leg the URL
  names (call-ID correlation).
- Bodies are capped at 64 KiB, WebSocket frames at 64 KiB, one relay socket per
  call, and the socket URL carries a single-use, short-lived token.

This app is the only thing exposed through a tunnel. It has no Desktop, admin or
secret routes; the Desktop REST namespace is served by Hermes itself.
"""
from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Optional
from urllib.parse import parse_qsl

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from ..engine.contracts import Direction
from ..engine.machine import RelayLink
from . import twilio_signature, twiml
from .ingress_auth import INBOUND_LIVE, LegExpectation, mask_sid, path_secret_ok

if TYPE_CHECKING:  # pragma: no cover
    from ..engine.service import CallService

MAX_BODY = 64 * 1024
MAX_FRAME = 64 * 1024
XML = "application/xml"


TERMINAL = {"completed", "busy", "no-answer", "failed", "canceled"}


def _route(path: str) -> str:
    parts = [p for p in path.split("/") if p]
    return "/".join(parts[3:-1]) if len(parts) > 4 else (parts[-1] if parts else "")


def build_app(svc: "CallService") -> Starlette:
    def reject(reason: str, *, call_id: Optional[str] = None, sid: str = "", route: str = "") -> None:
        svc.store.log_event(call_id, None, "ingress_rejected", {"reason": reason, "route": route,
                                                                "sid": mask_sid(sid)})

    def secret_ok(request) -> bool:
        if path_secret_ok(svc.auth.path_secret, request.path_params.get("secret", "")):
            return True
        reject("path_secret", route=_route(request.url.path))
        return False

    async def rest_verified(session, *, owner: bool = False, route: str = "") -> bool:
        """Fallback mode: Twilio's REST record of this leg must match (cached per leg)."""
        if svc.auth.signatures:
            return True
        want = svc.leg_expectation(session, owner=owner)
        ok, why = await asyncio.to_thread(svc.auth.verifier.verify, want)
        if not ok:
            reject(why, call_id=session.call_id, sid=want.sid, route=route)
        return ok

    async def rest_status(sid: str) -> Optional[str]:
        return await asyncio.to_thread(svc.auth.verifier.status, sid) if not svc.auth.signatures else None

    def auth_token() -> Optional[str]:
        try:
            return svc.get_secret("HERMES_LINE_TWILIO_AUTH_TOKEN")
        except Exception:
            return None

    def account_ok(params: dict) -> bool:
        try:
            expected = svc.get_secret("HERMES_LINE_TWILIO_ACCOUNT_SID")
        except Exception:
            expected = None
        return bool(expected) and params.get("AccountSid") == expected

    async def verified(request: Request) -> Optional[dict]:
        if not secret_ok(request):
            raise HTTPException(status_code=404)  # indistinguishable from a path that does not exist
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/x-www-form-urlencoded":
            return None
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_BODY:
                return None
        try:
            pairs = parse_qsl(body.decode("utf-8"), keep_blank_values=True, strict_parsing=bool(body))
        except (UnicodeDecodeError, ValueError):
            return None
        params = dict(pairs)
        route = _route(request.url.path)
        if svc.auth.signatures:
            query = request.url.query
            url = svc.public_url(request.url.path + (f"?{query}" if query else ""))
            if not twilio_signature.valid(auth_token(), url, pairs, request.headers.get("x-twilio-signature")):
                reject("signature", sid=params.get("CallSid", ""), route=route)
                return None
        if not account_ok(params):
            reject("account", sid=params.get("CallSid", ""), route=route)
            return None
        return params

    def forbidden() -> Response:
        return Response(status_code=403)

    def xml(body: str) -> Response:
        return Response(body, media_type=XML)

    async def leg(request: Request, *, owner: bool = False):
        params = await verified(request)
        if params is None:
            return None, None
        session = svc.sessions.get(request.path_params["call_id"])
        if session is None:
            return params, None
        sid = params.get("CallSid", "")
        expected = session.owner_sid if owner else session.provider_sid
        if not expected:  # the REST create may still be returning; give it a moment
            for _ in range(40):
                await asyncio.sleep(0.05)
                expected = session.owner_sid if owner else session.provider_sid
                if expected:
                    break
        if not sid or sid != expected:
            svc.store.log_event(session.call_id, None, "correlation_rejected", {"owner_leg": owner})
            return params, None
        if not await rest_verified(session, owner=owner, route=_route(request.url.path)):
            return params, None
        return params, session

    async def answer(request: Request) -> Response:
        params, session = await leg(request)
        if params is None:
            return forbidden()
        if session is None or session.terminal or session.direction is not Direction.OUTBOUND:
            return xml(twiml.response(twiml.hangup()))
        welcome = svc.cfg.disclosure() if svc.disclosure_on else None
        return xml(svc.current_relay_twiml(session, welcome=welcome))

    async def status(request: Request) -> Response:
        params, session = await leg(request)
        if params is None:
            return forbidden()
        if session is not None:
            claimed = params.get("CallStatus", "")
            if claimed in TERMINAL and not svc.auth.signatures:
                if await rest_status(session.provider_sid) not in TERMINAL:
                    reject("status_unconfirmed", call_id=session.call_id, sid=session.provider_sid, route="status")
                    return Response(status_code=204)
            await session.business_status(claimed)
        return Response(status_code=204)

    async def connect_action(request: Request) -> Response:
        params, session = await leg(request)
        if params is None:
            return forbidden()
        if session is None:
            return xml(twiml.response(twiml.hangup()))
        raw = request.query_params.get("g", "")
        generation = int(raw) if raw.isdigit() and len(raw) < 6 else None
        return xml(await session.connect_completed(generation, params))

    async def owner_answer(request: Request) -> Response:
        params, session = await leg(request, owner=True)
        if params is None:
            return forbidden()
        if session is None or session.terminal:
            return xml(twiml.response(twiml.say("This call has ended."), twiml.hangup()))
        return xml(twiml.response(twiml.owner_whisper(
            prompt=session.owner_whisper_text(),
            accept_url=svc.url(f"/twilio/voice/owner/accept/{session.call_id}"))))

    async def owner_accept(request: Request) -> Response:
        params, session = await leg(request, owner=True)
        if params is None:
            return forbidden()
        if session is not None and not svc.auth.signatures and await rest_status(session.owner_sid) != "in-progress":
            reject("owner_leg_not_answered", call_id=session.call_id, sid=session.owner_sid, route="owner/accept")
            return xml(twiml.response(twiml.hangup()))
        if session is None or not await session.owner_accept(params.get("Digits", "")):
            return xml(twiml.response(twiml.say("Okay. Goodbye."), twiml.hangup()))
        return xml(twiml.response(twiml.say("Connecting you now."), twiml.conference(
            name=session.conference, status_url=svc.url(f"/twilio/voice/conference/status/{session.call_id}"))))

    async def owner_status(request: Request) -> Response:
        params, session = await leg(request, owner=True)
        if params is None:
            return forbidden()
        if session is not None:
            claimed = params.get("CallStatus", "")
            if claimed in TERMINAL and not svc.auth.signatures:
                if await rest_status(session.owner_sid) not in TERMINAL:
                    reject("status_unconfirmed", call_id=session.call_id, sid=session.owner_sid, route="owner/status")
                    return Response(status_code=204)
            await session.owner_leg_status(claimed)
        return Response(status_code=204)

    async def conference_join(request: Request) -> Response:
        params, session = await leg(request)
        if params is None:
            return forbidden()
        if session is None or not session.conference:
            return xml(twiml.response(twiml.hangup()))
        return xml(twiml.response(session.conference_twiml()))

    async def conference_status(request: Request) -> Response:
        params = await verified(request)
        if params is None:
            return forbidden()
        session = svc.sessions.get(request.path_params["call_id"])
        if session is not None and params.get("FriendlyName") == session.conference:
            event = params.get("StatusCallbackEvent", "")
            if event == "conference-end" and not svc.auth.signatures:
                legs = [await rest_status(sid) for sid in (session.provider_sid, session.owner_sid) if sid]
                if not any(state in TERMINAL for state in legs):
                    reject("conference_end_unconfirmed", call_id=session.call_id, route="conference/status")
                    return Response(status_code=204)
            await session.conference_event(event, params)
        return Response(status_code=204)

    async def inbound(request: Request) -> Response:
        params = await verified(request)
        if params is None:
            return forbidden()
        if not svc.auth.signatures:
            want = LegExpectation(params.get("CallSid", ""), "inbound", svc.cfg.line_number, params.get("From", ""))
            ok, why = await asyncio.to_thread(svc.auth.verifier.verify, want, require_status=INBOUND_LIVE)
            if not ok:
                reject(why, sid=want.sid, route="inbound")
                return xml(twiml.response(twiml.reject("rejected")))
        return xml(await svc.on_inbound(params))

    async def digits(request: Request) -> Response:
        """DTMF fallback (L5): ``<Play digits>`` then a new stream, for a one-time nonce only."""
        params, session = await leg(request)
        if params is None:
            return forbidden()
        if session is None or session.terminal or session.voice is None:
            return xml(twiml.response(twiml.hangup()))
        return xml(svc.digits_twiml(session, request.path_params["nonce"]))

    async def media(ws: WebSocket) -> None:
        """Twilio Media Stream for the realtime voice path (L5): authenticated exactly like the relay."""
        call_id = ws.path_params["call_id"]
        if not secret_ok(ws):
            await ws.close(code=1008)
            return
        url = svc.public_ws_url(ws.url.path)  # the token is in the path; <Stream url> takes no query
        if svc.auth.signatures and not twilio_signature.valid(auth_token(), url, [], ws.headers.get("x-twilio-signature")):
            reject("signature", call_id=call_id, route="media")
            await ws.close(code=1008)
            return
        session = svc.sessions.get(call_id)
        if session is None or session.voice is None or not await rest_verified(session, route="media"):
            reject("media_session", route="media")
            await ws.close(code=1008)
            return
        if svc.claim_media(call_id, ws.path_params["token"]) is None:
            reject("media_token", route="media")
            await ws.close(code=1008)
            return
        await ws.accept()
        link = RelayLink(session.voice.engine_frame, session.voice.engine_close)
        if not session.attach_relay(link):
            await ws.close(code=1008)
            return
        try:
            await session.voice.serve(ws)
        except Exception:
            pass
        finally:
            link.closed = True
            session.relay_closed(link)

    async def health(request: Request) -> Response:
        return JSONResponse({"ok": svc.status == "ready"})

    async def relay(ws: WebSocket) -> None:
        call_id = ws.path_params["call_id"]
        if not secret_ok(ws):
            await ws.close(code=1008)
            return
        query = ws.url.query
        url = svc.public_ws_url(ws.url.path + (f"?{query}" if query else ""))
        token = ws.query_params.get("t", "")
        if svc.auth.signatures and not twilio_signature.valid(auth_token(), url, [], ws.headers.get("x-twilio-signature")):
            reject("signature", call_id=call_id, route="relay")
            await ws.close(code=1008)
            return
        session = svc.claim_relay(call_id, token)
        if session is None:
            reject("relay_token", route="relay")
            await ws.close(code=1008)
            return
        await ws.accept()
        link = RelayLink(ws.send_json, lambda code: ws.close(code=code))
        if not session.attach_relay(link):
            await ws.close(code=1008)
            return
        try:
            while True:
                text = await ws.receive_text()
                if len(text) > MAX_FRAME:
                    break
                try:
                    frame = json.loads(text)
                except ValueError:
                    continue
                if isinstance(frame, dict) and isinstance(frame.get("type"), str):
                    session.offer(frame, link)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            link.closed = True
            session.relay_closed(link)

    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/t/{secret}/twilio/voice/answer/{call_id}", answer, methods=["POST"]),
        Route("/t/{secret}/twilio/voice/status/{call_id}", status, methods=["POST"]),
        Route("/t/{secret}/twilio/voice/connect/action/{call_id}", connect_action, methods=["POST"]),
        Route("/t/{secret}/twilio/voice/owner/answer/{call_id}", owner_answer, methods=["POST"]),
        Route("/t/{secret}/twilio/voice/owner/accept/{call_id}", owner_accept, methods=["POST"]),
        Route("/t/{secret}/twilio/voice/owner/status/{call_id}", owner_status, methods=["POST"]),
        Route("/t/{secret}/twilio/voice/conference/join/{call_id}", conference_join, methods=["POST"]),
        Route("/t/{secret}/twilio/voice/conference/status/{call_id}", conference_status, methods=["POST"]),
        Route("/t/{secret}/twilio/voice/inbound", inbound, methods=["POST"]),
        Route("/t/{secret}/twilio/voice/digits/{call_id}/{nonce}", digits, methods=["POST"]),
        WebSocketRoute("/t/{secret}/twilio/media/{call_id}/{token}", media),
        WebSocketRoute("/t/{secret}/twilio/relay/{call_id}", relay),
    ]
    return Starlette(routes=routes)
