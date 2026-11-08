"""Offline readiness probe; observes blockers, does not certify hold readiness."""
import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from plugin.line.engine.machine import CallSession
from plugin.line.engine.contracts import Mode, CallState


async def main():
    session = SimpleNamespace(mode=Mode.OWNER, state=CallState.ON_HOLD,
        last_confirm=time.monotonic(), unclear_replies=1,
        cfg=SimpleNamespace(confirm_reply_window_s=60),
        _event=Mock(), _on_human=AsyncMock())
    await CallSession._maybe_confirm(session, 'unrecognized fragment')
    assumed = session._on_human.await_count == 1
    assert assumed, 'baseline changed: re-evaluate L1 readiness'
    menu = SimpleNamespace(menu_keys_allowed=lambda: True, digits_gen=-1, gen=1,
        voice=None, _send=Mock(), _transcript=Mock(), _event=Mock(),
        task=SimpleNamespace(goal='Decline all callback offers; stay on the line'),
        last_detection=SimpleNamespace(label='ivr', evidence=('callback offer: press 1',)))
    out = await CallSession.press_digits(menu, '1')
    assert out.get('ok') and menu._send.called, 'baseline changed: re-evaluate callback restriction hook'
    print(json.dumps({'readiness': 'blocked', 'owner_hold_unclear_reply_promoted_to_human': assumed,
                      'callback_digit_not_blocked_by_task_guidance': out['ok']}))

asyncio.run(main())
