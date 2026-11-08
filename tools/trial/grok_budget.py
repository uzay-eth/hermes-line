"""Trial-local conservative xAI meter; no secrets, provider totals or audio persisted."""
import asyncio
import json
import fcntl
import time
from decimal import Decimal
from pathlib import Path
from inbound import durable_save

VOICE = Decimal('0.08') / 60
STT = Decimal('0.20') / 3600
TEXT = Decimal('0.004')
CEILING = Decimal('1.80')
BILLING = 'No documented billing read for an inference key; Management API requires a separate management key.'


class BudgetError(RuntimeError):
    pass


def number(value):
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise BudgetError('invalid ledger amount')
    return result


def ledger(root):
    path = Path(root) / 'xai-spend.json'
    value = json.loads(path.read_text()) if path.exists() else {'version': 1, 'runs': {}}
    if value['version'] != 1 or not isinstance(value['runs'], dict):
        raise BudgetError('invalid ledger')
    # A pending reservation is never discarded automatically after process death.
    total = sum((number(row['usd']) for row in value['runs'].values()), Decimal(0))
    return value, total


def summary(root):
    value, total = ledger(root)
    return {'accounted_usd': str(total), 'ceiling_usd': str(CEILING),
            'pending_runs': sum(row['state'] == 'pending' for row in value['runs'].values()),
            'billing': BILLING, 'basis': 'ADR 0004 connected-time estimate, not provider billing'}


def reconcile_crashes(root):
    """Called only after acquiring the global trial lock: no older run is active.

    A pending journal represents an interrupted run, not unused credit. Charge
    its whole reservation (including previously debited text), never release it.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'xai-spend.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data, _ = ledger(root)
        recovered = []
        for ident, row in data['runs'].items():
            if row['state'] == 'pending':
                floor = number(row['worst_case_usd']) + TEXT * number(row.get('text_inputs', 0))
                row.update(state='reconciled_crash', usd=str(max(number(row['usd']), floor)))
                recovered.append(ident)
        if recovered:
            durable_save(root / 'xai-spend.json', data)
    return recovered



class GrokRun:
    """Caller holds the harness's global run lock for this object's entire lifetime."""
    def __init__(self, root, run_dir, seconds, dry=False):
        self.root, self.run_dir, self.seconds, self.dry = Path(root), Path(run_dir), seconds, dry
        self.ident = self.run_dir.name
        self.before = summary(root)
        self.data, total = ledger(root)
        self.reserved = number(seconds) * (VOICE + (Decimal(0) if dry else STT))
        if total + self.reserved > CEILING:
            raise BudgetError('xAI ledger plus worst case exceeds $1.80')
        self.texts = 0
        self.grok_seconds = self.stt_seconds = 0.0
        self.connect_ms = []
        self.created_fields = []
        self.sockets = set()
        self.connecting = set()
        self.finished = False
        self.expires = None
        self.data['runs'][self.ident] = {'state': 'pending', 'usd': str(self.reserved),
            'worst_case_usd': str(self.reserved), 'seconds_limit': seconds, 'dry': dry}
        self.persist()

    def persist(self):
        # Late close callbacks may raise this run's charge after a later run has
        # reserved funds. Merge only our row; never overwrite newer journal rows.
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / 'xai-spend.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            latest, _ = ledger(self.root)
            row = dict(self.data['runs'][self.ident])
            old = latest['runs'].get(self.ident)
            if old and old['state'] != 'pending':
                if row['state'] == 'pending':
                    row['state'] = old['state']
                row['usd'] = str(max(number(old['usd']), number(row['usd'])))
            latest['runs'][self.ident] = row
            durable_save(self.root / 'xai-spend.json', latest)
            self.data = latest

    def measured_cost(self):
        cost = number(self.grok_seconds) * VOICE + number(self.stt_seconds) * STT + TEXT * self.texts
        return cost.quantize(Decimal('0.00000001'), rounding='ROUND_UP')

    def accounting_floor(self):
        row = self.data['runs'][self.ident]
        row.update(usd=str(max(number(row['usd']), self.measured_cost())),
                   grok_seconds=self.grok_seconds, stt_seconds=self.stt_seconds, text_inputs=self.texts)
        self.persist()

    def reconcile_shutdown(self):
        # No awaits/new tasks on a service loop that has already been torn down.
        # Running close tasks remain unresolved: finish must retain the reservation.
        for sock in self.sockets:
            if sock.loop.is_closed():
                sock.abandon()


    def text_input(self):
        if self.finished:
            raise BudgetError('run already settled')
        row = self.data['runs'][self.ident]
        cost = self.reserved + TEXT * (self.texts + 1)
        other = sum((number(v['usd']) for k, v in self.data['runs'].items() if k != self.ident), Decimal(0))
        if other + cost > CEILING:
            raise BudgetError('xAI text input would exceed reserved budget')
        self.texts += 1
        row['usd'] = str(max(number(row['usd']), cost))
        row['text_inputs'] = self.texts
        self.persist()  # charge before send, including ambiguous failures

    async def connector(self, url, headers):
        if self.finished:
            raise BudgetError('run already settled')
        from plugin.line.voice.provider import websockets_connector
        kind = 'stt' if '/v1/stt?' in url else 'grok'
        if self.dry and (kind != 'grok' or self.connect_ms):
            raise BudgetError('dry permits only one realtime connection and no STT')
        if self.expires is None:
            self.expires = time.monotonic() + self.seconds
        remaining = self.expires - time.monotonic()
        if remaining <= 0 or kind in self.connecting or any(s.kind == kind and not s.closed for s in self.sockets):
            raise BudgetError('xAI time/concurrency reservation exhausted')
        started = time.monotonic()
        # Include connection establishment conservatively in measured seconds.
        self.connecting.add(kind)
        try:
            ws = await asyncio.wait_for(websockets_connector(url, headers), remaining)
        except BaseException:
            elapsed = max(0, time.monotonic() - started)
            if kind == 'grok':
                self.grok_seconds += elapsed
            else:
                self.stt_seconds += elapsed
            raise
        finally:
            self.connecting.discard(kind)
        self.connect_ms.append(round((time.monotonic() - started) * 1000, 1))
        socket = MeteredSocket(self, ws, kind, started)
        self.sockets.add(socket)
        return socket

    async def close_all(self):
        try:
            await asyncio.gather(*(s.close() for s in list(self.sockets)), return_exceptions=True)
        finally:
            # Cancellation of the gather/owner/inner close cannot skip booking.
            for sock in self.sockets:
                if sock.closed:
                    sock.account(time.monotonic() if not sock.close_complete else sock.stop_at)

    def finish(self):
        if self.connecting:
            raise BudgetError('cannot settle with xAI setup in flight')
        for sock in self.sockets:
            if sock.closed:
                sock.account(time.monotonic() if not sock.close_complete else sock.stop_at)
        if any(not sock.closed or sock.closing for sock in self.sockets):
            raise BudgetError('cannot settle with xAI connection or close in flight')
        cost = self.measured_cost()
        if self.finished:
            cost = max(cost, number(self.data['runs'][self.ident]['usd']))
        self.data['runs'][self.ident].update(state='settled', usd=str(cost),
            grok_seconds=self.grok_seconds, stt_seconds=self.stt_seconds, text_inputs=self.texts)
        self.persist()
        self.finished = True
        cost = number(self.data['runs'][self.ident]['usd'])
        durable_save(self.run_dir / 'grok-evidence.json', {'grok_connected_seconds': self.grok_seconds,
            'stt_connected_seconds': self.stt_seconds, 'connect_ms': self.connect_ms,
            'session_created_field_names': self.created_fields, 'text_inputs': self.texts,
            'ledger_before': self.before, 'ledger_after': summary(self.root), 'estimated_usd': str(cost),
            'first_audio_latency': None if self.dry else 'see voice_first_audio events',
            'no_audio_sent': self.dry, 'stt_stream_started': self.stt_seconds > 0})


class MeteredSocket:
    def __init__(self, run, ws, kind, start):
        self.run, self.ws, self.kind, self.start = run, ws, kind, start
        self.closed = False
        self.closing = False
        self.close_complete = False
        self.stop_at = None
        self._accounted = 0.0
        self._closer = None
        self.loop = asyncio.get_running_loop()
        self.timer = asyncio.create_task(self.expire())

    async def expire(self):
        await asyncio.sleep(max(0, self.run.expires - time.monotonic()))
        await self.close()

    async def send(self, data):
        if self.closed:
            raise BudgetError('xAI connection expired')
        if isinstance(data, str):
            event = json.loads(data)
            if self.run.dry and event.get('type') != 'session.update':
                raise BudgetError('dry permits session configuration only')
            if event.get('type') == 'conversation.item.create':
                self.run.text_input()
        elif self.run.dry:
            raise BudgetError('dry audio prohibited')
        await self.ws.send(data)

    async def recv(self):
        raw = await self.ws.recv()
        if isinstance(raw, str):
            event = json.loads(raw)
            if event.get('type') == 'session.created':
                # Names only. Event values can include session IDs and instructions.
                self.run.created_fields = sorted(event.keys()) + ['session.' + k for k in sorted((event.get('session') or {}).keys())]
        return raw

    def account(self, stopped):
        seconds = max(self._accounted, max(0.0, stopped - self.start))
        delta = seconds - self._accounted
        self._accounted = seconds
        if self.kind == 'grok':
            self.run.grok_seconds += delta
        else:
            self.run.stt_seconds += delta
        # Durably protect this floor even while asynchronous cleanup is suspended.
        self.run.accounting_floor()

    def abort(self):
        transport = getattr(getattr(self.ws, '_ws', None), 'transport', None)
        if transport is not None:
            transport.abort()

    def abandon(self):
        if self.close_complete:
            return
        # Used after service-loop teardown, including a close task cancelled
        # before it ran. The wall-time fallback cannot refund an earlier charge.
        try:
            self.abort()
        finally:
            self.closed, self.closing = True, False
            self.account(time.monotonic())

    async def close(self):
        if self.closing:
            # Join the first operation; cancelling a waiter must not cancel it.
            await asyncio.shield(self._closer)
            return
        if self.close_complete:
            return
        self._closer = asyncio.current_task()
        self.closed, self.closing = True, True
        try:
            if asyncio.current_task() is not self.timer:
                self.timer.cancel()
            try:
                self.abort()
            finally:
                self.stop_at = time.monotonic()
                self.account(self.stop_at)  # synchronous, before the first await
            try:
                await asyncio.wait_for(self.ws.close(), .2)
                self.close_complete = True
            except Exception:
                pass  # incomplete close: finish uses conservative wall time
        finally:
            try:
                self.account(self.stop_at if self.close_complete else time.monotonic())
            finally:
                self.closing = False
            # A real close arriving after fallback settlement may only raise it.
            if self.run.finished:
                self.run.finish()


async def dry_connect(run, get_secret):
    from plugin.line.voice.grok import GrokRealtimeProvider, DEFAULT_MODEL
    from plugin.line.voice.provider import SessionConfig
    p = GrokRealtimeProvider(get_secret, connector=run.connector, ready_timeout_s=4)
    began = time.monotonic()
    try:
        await asyncio.wait_for(p.connect(SessionConfig('conversation', 'Audio-free trial configuration check.',
            'eve', [], False, reasoning_effort='none')), 4.8)
        return {'model': DEFAULT_MODEL, 'session_update_accepted': True,
                'connect_and_config_ms': round((time.monotonic() - began) * 1000, 1)}
    finally:
        await run.close_all()
