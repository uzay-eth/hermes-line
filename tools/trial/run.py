#!/usr/bin/env python3
"""Attended trial harness. Provider writes require coordinator-only `call` or `listen` (plus pending restoration)."""
from __future__ import annotations
import argparse
import asyncio
import contextlib
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
from types import SimpleNamespace
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from decimal import Decimal

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
ROOT = Path('/Users/mac/.hermes/cache/scratch/hermes-line-trial')
SOURCE = Path('/Users/mac/.hermes/hermes-agent')
PYTHON = SOURCE / 'venv/bin/python'
VIEWER_PYTHON = Path('/Users/mac/.hermes/cache/scratch/hermes-line-l1/run/venv/bin/python')
sys.path.insert(0, str(REPO))
from tools.trial.personal_numbers import load_personal_numbers, load_line_number
PERSONAL_NUMBERS_PATH = None
ALLOW = ()


def initialize_numbers():
    global ALLOW
    numbers = load_personal_numbers(PERSONAL_NUMBERS_PATH)
    ALLOW = (numbers['owner'], numbers['fi'])


BORA = '+17755550172'  # the author's protected number; a fictional 555 value in the public copy
CALL_PROFILE = 'hermes-line-calls'
HASH = '66acb92392ed3981025c349d437230aca3ae27422780692aa3ef749e2fb4321e'
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(Path(__file__).parent))
from inbound import VoiceSettings, InboundTrial
from grok_budget import GrokRun, summary as xai_summary, dry_connect, reconcile_crashes
from plugin.line.engine.config import EngineConfig
from plugin.line.engine.contracts import CallState, Direction, Mode, Role, GateRejected
from plugin.line.engine.service import CallService
from plugin.line.engine.telephony import TwilioRestAdapter, ProviderError
from plugin.line.engine.acp_driver import AcpDriver
from plugin.line.engine.redact import mask_text
from plugin.line.store import Store


class Stop(RuntimeError):
    pass


def emit(value):
    print(json.dumps(value, sort_keys=True), flush=True)


def private_dir(path):
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)


def save(path, value):
    private_dir(path.parent)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def error_payload(exc):
    # Only explicitly safe exception types expose text; arbitrary provider/ACP
    # exceptions can contain authorization headers or bearer URLs.
    if isinstance(exc, GateRejected):
        gate, reason = masked(str(exc.gate)), masked(str(exc.reason))
        return {'ok':False, 'error':gate+': '+reason, 'error_type':'GateRejected',
                'gate':gate, 'reason':reason}
    return {'ok':False, 'error':masked(str(exc)) if isinstance(exc, Stop) else type(exc).__name__}


def report_error(exc, run_dir=None):
    payload = error_payload(exc)
    if run_dir is not None:
        payload['run_dir'] = str(run_dir)
        save(run_dir/'error.json', payload)
    emit(payload)
    exc._trial_error_reported = True
    return payload


def validate_call(to, coordinator, minutes):
    # Must run before opening credentials or making ANY API request.
    if to == BORA or to not in ALLOW:
        raise Stop('destination refused: use an exact allowlisted E.164 number')
    if not coordinator:
        raise Stop('call requires --i-am-the-coordinator')
    if type(minutes) is not int or not 1 <= minutes <= 3:
        raise Stop('attended trial max-minutes must be 1-3')


def owner_pin_status():
    return 'present' if (Path.home() / '.hermes-line/owner_pin').exists() else 'absent'


def load_owner_pin(home):
    path = home / 'owner_pin'
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return None
    if mode & 0o777 != 0o600:
        raise Stop('owner PIN file must have mode 0600')
    try:
        raw = path.read_text()  # one read; never included in diagnostics
    except (OSError, UnicodeError):
        raise Stop('owner PIN file could not be read') from None
    if not re.fullmatch(r'[0-9]{4,8}\n?', raw):
        raise Stop('owner PIN file must contain one line of 4-8 digits')
    return raw.rstrip('\n')


def credentials(grok=False, owner_pin=False):
    home = Path.home() / '.hermes-line'
    account = (home / 'twilio_account_sid').read_text().strip()
    key = json.loads((home / 'twilio_api_key.json').read_text())
    line = load_line_number(home / 'twilio_line_number')
    values = dict(HERMES_LINE_TWILIO_ACCOUNT_SID=account,
                  HERMES_LINE_TWILIO_API_KEY_SID=key['api_key_sid'],
                  HERMES_LINE_TWILIO_API_KEY_SECRET=key['api_key_secret'])
    if (not re.fullmatch(r'AC[0-9a-f]{32}', account)
            or not re.fullmatch(r'SK[0-9a-f]{32}', values['HERMES_LINE_TWILIO_API_KEY_SID'])
            or not re.fullmatch(r'[A-Za-z0-9]{32}', values['HERMES_LINE_TWILIO_API_KEY_SECRET'])
            or not re.fullmatch(r'\+1[0-9]{10}', line) or line == BORA or line in ALLOW):
        raise Stop('credential or plugin line validation failed')
    if grok:
        keyfile = home / 'xai_api_key'
        if keyfile.stat().st_mode & 0o777 != 0o600:
            raise Stop('xAI key file must have mode 0600')
        raw = keyfile.read_text()  # exactly one read per grok invocation
        if len(raw.splitlines()) != 1 or not raw.strip():
            raise Stop('xAI key file must contain one nonempty line')
        values['XAI_API_KEY'] = raw.strip()
        raw = ''
    # Explicit empty optional secret prevents fallback to an inherited process PIN.
    values['HERMES_LINE_OWNER_PIN'] = ''
    if owner_pin:
        pin = load_owner_pin(home)
        if pin is not None:
            values['HERMES_LINE_OWNER_PIN'] = pin
    # The plugin's declared requires_env names, resolved through real secret_scope
    # in every service/worker thread. No dotenv or key file is copied to the repo.
    from agent.secret_scope import set_secret_scope, reset_secret_scope, get_secret
    def scoped(name):
        token = set_secret_scope(values, profile_home=os.environ['HERMES_HOME'])
        try:
            return get_secret(name)
        finally:
            reset_secret_scope(token)
    return scoped, line


def balance(adapter, require_floor=True):
    data = adapter._post('Balance.json', None)
    amount = Decimal(str(data.get('balance', 'NaN')))
    if (data.get('currency', '').upper() != 'USD' or not amount.is_finite()
            or (require_floor and amount < Decimal('5'))):
        raise Stop('balance stop: require USD balance >= $5.00')
    return {'usd': str(amount), 'at': time.time()}


def caller_id(choice, line):
    """Caller ID shown on the business leg: the plugin line, or the owner's Twilio-verified Fi line.

    The owner leg and callbacks always use the plugin line (L1 machine.py owner leg)."""
    if choice == 'line':
        return line
    if choice == 'fi':
        return ALLOW[1]
    raise Stop('caller id must be line or fi')


def config(line, minutes=3, caller=None, transport="relay"):
    return EngineConfig.load(dict(enabled=True, public_base_url='https://trial.invalid',
        listen_host='127.0.0.1', listen_port=0, from_number=caller or line, line_number=line,
        owner_number=ALLOW[0], owner_name='Can', max_call_minutes=minutes, call_profile=CALL_PROFILE,
        sms_source='none', otp_browser_fill=False, ingress_auth='auto',
        voice=({'transport': 'conversation_relay'} if transport == 'relay' else
               {'transport': 'media_streams', 'reasoning_effort': 'none'}), policy={
            'allowed_destinations': list(ALLOW), 'allowlist_only': True,
            'blocked_destinations': [BORA],
            'require_approval': True, 'disclosure_enabled': True,
            'recording_enabled': False, 'quiet_hours_enabled': True,
            'owner_timezone': 'America/Los_Angeles', 'max_calls_per_line': 1}))


class TrialAdapter(TwilioRestAdapter):
    """Use the production form encoder even in dry; intercept only the HTTP boundary.

    At most one create attempt (not one success) per invocation. Persist before
    network I/O so an ambiguous create timeout is never retried by this process.
    """
    def __init__(self, get_secret, *, real=False, to=None, minutes=3, run_dir=None):
        super().__init__(get_secret)
        self.live = real
        self.real, self.to, self.minutes = real, to, minutes
        self.run_dir = run_dir
        self.attempted = False
        self.forms, self.records = [], {}
        self.lock = threading.Lock()
        self.cancelled = threading.Event()

    def _create_call(self, **kw):
        with self.lock:
            if self.cancelled.is_set():
                raise ProviderError('trial shutdown requested')
            if kw['to'] not in ALLOW or kw['to'] == BORA or self.attempted:
                raise ProviderError('trial permits exactly one allowlisted outbound leg')
            if self.real and kw['to'] != self.to:
                raise ProviderError('trial destination changed')
            if kw['time_limit_s'] != self.minutes * 60:
                raise ProviderError('provider duration cap mismatch')
            self.attempted = True
            if self.real:
                # Re-read at the final provider boundary, after owner approval.
                before = balance(self)
                if self.cancelled.is_set():
                    raise ProviderError('trial shutdown requested during balance check')
                save(self.run_dir / 'balance-before.json', before)
                save(self.run_dir / 'dial-attempt.json', {'at': time.time(), 'retry': False})
        return super()._create_call(**kw)

    def _post(self, path, params):
        if self.real:
            return super()._post(path, params)
        if path == 'Calls.json' and params is not None:
            form = dict(params)
            self.forms.append(form)
            sid = 'CA' + uuid.uuid4().hex
            self.records[sid] = dict(sid=sid, account_sid=self._get_secret('HERMES_LINE_TWILIO_ACCOUNT_SID'),
                direction='outbound-api', to=form['To'], **{'from': form['From']}, status='in-progress')
            return {'sid': sid}
        match = re.fullmatch(r'Calls/(CA[0-9a-f]{32})\.json', path)
        if match:
            if params:
                self.records[match[1]]['status'] = 'completed'
            return self.records.get(match[1], {})
        raise ProviderError('dry adapter refuses this API route')


def approved_tasks(store, parent_id):
    if not parent_id:
        return []
    return list(dict.fromkeys(e['task_id'] for e in store.events(parent_id)
                if e['kind'] == 'owner_voice_approved' and e['task_id']))


def ringback_latencies(events):
    states = {}
    for event in events:
        if event['kind'] == 'state':
            states.setdefault(event['data'].get('to'), event['ts'])
    def delta(a, b):
        return max(0, states[b] - states[a]) if a in states and b in states else None
    return {'human_to_owner_connected_s': delta('human_detected', 'owner_connected'),
            'ring_to_owner_connected_s': delta('ringing_owner', 'owner_connected'),
            'transcript_to_human_s': None,
            'note': 'state-event times; transcript-to-human timing unavailable'}


def ringback_done(store, parent_id):
    if not parent_id or not store.get_call(parent_id).state.terminal:
        return False
    for tid in approved_tasks(store, parent_id):
        task = store.get_task(tid)
        calls = store.calls_for_task(tid)
        if task is None or task.status.open or not calls or any(not c.state.terminal for c in calls):
            return False
    return True


class RingbackAdapter(TrialAdapter):
    """Listener-only scope, backed by engine-issued permits and parent approval events."""
    def __init__(self, get_secret, *, line, minutes, run_dir, real=True):
        super().__init__(get_secret, real=real, minutes=minutes, run_dir=run_dir)
        self.line = line
        self.store = None
        self.parent = lambda: None
        self.deadline = time.monotonic() + minutes * 60
        self.attempts = set()
        self.children = {}
        self.context = threading.local()

    def place_call(self, permit, **kw):
        # Let the base class validate/mint-check/consume the original engine permit.
        # The per-thread context is used only within that synchronous dispatch.
        self.context.permit = permit
        try:
            return super().place_call(permit, **kw)
        finally:
            self.context.permit = None

    def _create_call(self, **kw):
        permit = getattr(self.context, 'permit', None)
        with self.lock:
            parent = self.parent()
            if self.cancelled.is_set() or not permit or not self.store or not parent:
                raise ProviderError('listener create has no active approved scope')
            events = self.store.events(parent)
            task = self.store.get_task(permit.task_id)
            call = self.store.get_call(permit.call_id)
            if (kw['to'] not in ALLOW or kw['to'] in (BORA, self.line)
                    or permit.task_id not in approved_tasks(self.store, parent)
                    or not any(e['kind'] == 'owner_verified' for e in events)
                    or task is None or call is None or call.task_id != task.id
                    or not self.store.get_call(parent).state.terminal):
                raise ProviderError('listener create outside approved owner task')
            role = permit.role
            if role == Role.BUSINESS:
                if kw['to'] != task.destination_e164:
                    raise ProviderError('listener business destination changed')
            elif role == Role.OWNER:
                if (kw['to'] != ALLOW[0] or self.children.get(task.id) != call.id
                        or (task.id, Role.BUSINESS) not in self.attempts):
                    raise ProviderError('listener owner leg outside child task')
            else:
                raise ProviderError('listener role refused')
            key = (task.id, role)
            remaining = int(self.deadline - time.monotonic())
            if key in self.attempts or remaining < 1:
                raise ProviderError('listener retry or expired trial refused')
            self.attempts.add(key)  # ambiguous failures are never retried
            if role == Role.BUSINESS:
                self.children[task.id] = call.id
            if self.real:
                try:
                    funds = balance(self, require_floor=False)
                except Exception:
                    self.cancelled.set()
                    raise
                if Decimal(funds['usd']) <= Decimal('4'):
                    self.cancelled.set()
                    raise ProviderError('listener balance reached $4.00 stop line')
                save(self.run_dir / ('balance-create-' + str(len(self.attempts)) + '.json'), funds)
            remaining = int(self.deadline - time.monotonic())
            if self.cancelled.is_set() or remaining < 1:
                raise ProviderError('listener stopped before dispatch')
            kw['time_limit_s'] = min(kw['time_limit_s'], remaining)
            save(self.run_dir / 'ringback-attempts.json',
                 {'attempts': [{'task_id': tid, 'role': r.value} for tid, r in sorted(self.attempts, key=str)],
                  'retry': False})
        return TwilioRestAdapter._create_call(self, **kw)


class QuietDriver:
    """No gateway attachment or model in preflight's synthetic stream probe."""
    def set_mcp_servers(self, servers):
        pass
    def start(self, briefing):
        pass
    def ask(self, *args):
        return ''
    def close(self):
        pass
    def note(self, *args):
        pass


def viewer_env(run_dir, profile=CALL_PROFILE):
    # Match L1's existing scratch interpreter/site-packages setup. It avoids the
    # installed interpreter's bootstrap lock in the owner's install directory.
    site = '/Users/mac/.hermes/installs/778930d1ee3bddda/environments/36f5841acd484742aba00d117c04db01/venv/lib/python3.14/site-packages'
    return dict(PATH='/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin', HOME=str(Path.home()),
        HERMES_HOME=str(run_dir / 'viewerboot'), TMPDIR=str(run_dir), TRIAL_CALL_PROFILE=profile,
        TRIAL_SCOPE_REPORT=str(run_dir / 'scope.jsonl'),
        PYTHONDONTWRITEBYTECODE='1', HERMES_DISABLE_LAZY_INSTALLS='1',
        PYTHONPATH=str(SOURCE) + ':' + site, HERMES_AGENT_ROOT=str(SOURCE))


def viewer_command(run_dir, check=False):
    sandbox = run_dir / 'viewer.sb'
    sandbox.write_text('(version 1)\n(allow default)\n'
        f'(deny file-write* (require-not (subpath "{run_dir}")))\n'
        '(allow file-write* (literal "/dev/null") (literal "/dev/tty"))\n')
    return ['/usr/bin/sandbox-exec', '-f', str(sandbox), str(VIEWER_PYTHON), '-B',
            str(Path(__file__).with_name('viewer.py'))] + (['--check'] if check else [])


def gateway_check(run_dir):
    result = subprocess.run(viewer_command(run_dir, True), env=viewer_env(run_dir),
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
    if result.returncode:
        raise Stop('no ready owner gateway; refusing to start one')


class Driver(AcpDriver):
    def __init__(self, sid, run_dir, dry=False, profile=CALL_PROFILE):
        if profile != CALL_PROFILE:
            raise Stop('trial driver requires the restricted call profile')
        super().__init__(sid, command=viewer_command(run_dir), env=viewer_env(run_dir, profile),
            cwd=str(run_dir), start_timeout_s=60, turn_timeout_s=90)
        self.dry = dry
        self.start_ms = None
        self.steps = []
        self.title = None

    def start(self, briefing):
        t = time.monotonic()
        super().start(briefing)
        # Hermes session titles are unique per profile; a fixed title fails from the second run on.
        title = ('hermes-line trial dry ' if self.dry else 'hermes-line trial ') + time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        self.title = title
        res = self._prompt(self.hermes_session_id, '/title ' + title, None, 30)
        if res.error:
            raise Stop('ACP title failed: ' + str(res.error)[:160])
        self.start_ms = round((time.monotonic() - t) * 1000, 1)

    def _on_notification(self, method, params):
        update = params.get('update') or {}
        if method == 'session/update' and update.get('sessionUpdate') in ('tool_call', 'tool_call_update'):
            # Keep timings and tool names only. Never persist arguments, bearer specs or raw output.
            title = str(update.get('title', ''))
            names = re.findall(r'tool_search|tool_call|hermes_line_call_(?:finish|press_digits|bring_owner)', title)
            self.steps.append({'at': time.monotonic(), 'kind': update.get('sessionUpdate'),
                               'title': ' '.join(names) or '[other tool]',
                               'status': update.get('status'), 'id': update.get('toolCallId')})
        super()._on_notification(method, params)

    def ask(self, prompt, timeout_s=20):
        # Dry must create exactly one saved session. Detection isn't exercised.
        return '' if self.dry else super().ask(prompt, timeout_s)


class Tunnel:
    def __init__(self, port):
        self.proc = None
        self.url = None
        self.port = port
        self.ready = threading.Event()

    def start(self):
        exe = shutil.which('cloudflared')
        if not exe:
            raise Stop('cloudflared missing; install with brew install cloudflared')
        self.proc = subprocess.Popen([exe, 'tunnel', '--no-autoupdate', '--url',
            f'http://127.0.0.1:{self.port}', '--protocol', 'http2'],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        def read():
            for line in self.proc.stderr:
                match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', line)
                if match:
                    self.url = match[0]
                    self.ready.set()
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        if not self.ready.wait(40):
            raise Stop('quick tunnel did not publish a hostname')
        return self.url

    def stop(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
            self.reader.join(timeout=2)
            self.proc.stderr.close()


class TunnelDNS:
    """Process-only resolver fallback for the one quick-tunnel hostname.

    Some local DNS filters return NXDOMAIN for trycloudflare.com. Query Cloudflare's
    resolver for that exact tunnel only; preserve the URL hostname, TLS SNI and
    certificate validation. No /etc/hosts, VPN or system DNS changes.
    """
    def __init__(self, url):
        self.host = url.split('://')[1]
        self.original = socket.getaddrinfo
        self.mode = 'system'

    def __enter__(self):
        try:
            self.original(self.host, 443)
        except socket.gaierror:
            addresses = []
            deadline = time.monotonic() + 45
            while not addresses and time.monotonic() < deadline:
                answer = subprocess.run(['/usr/bin/dig', '@1.1.1.1', '+short', '+time=2', '+tries=1',
                    self.host, 'A'], capture_output=True, text=True, timeout=5, check=True)
                for row in answer.stdout.splitlines():
                    try:
                        addr = ipaddress.ip_address(row.strip())
                        if addr.version == 4 and addr.is_global:
                            addresses.append(str(addr))
                    except ValueError:
                        pass
                if not addresses:
                    time.sleep(1)
            if not addresses:
                raise Stop('quick tunnel DNS failed through system and Cloudflare resolver: ' + self.host)
            def resolve(host, port, *args, **kwargs):
                return self.original(addresses[0] if host == self.host else host, port, *args, **kwargs)
            socket.getaddrinfo = resolve
            self.mode = 'Cloudflare 1.1.1.1, tunnel hostname only (system NXDOMAIN)'
        return self

    def __exit__(self, *args):
        socket.getaddrinfo = self.original


def health(url):
    # Explicit proxy bypass; do not send bearer URLs through an inherited proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 40
    failure = 'unknown'
    while True:
        try:
            with opener.open(url + '/health', timeout=8) as response:
                if json.load(response).get('ok'):
                    return
        except Exception as exc:
            failure = type(exc).__name__ + ':' + type(getattr(exc, 'reason', None)).__name__
        if time.monotonic() >= deadline:
            raise Stop('public HTTPS health check failed (' + failure + '; ' + url + ')')
        time.sleep(1)


def post_answer(svc, call):
    import urllib.parse
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = svc.url('/twilio/voice/answer/' + call.id)
    data = urllib.parse.urlencode({'AccountSid': svc.get_secret('HERMES_LINE_TWILIO_ACCOUNT_SID'),
        'CallSid': call.provider_sid, 'CallStatus': 'in-progress'}).encode()
    with opener.open(urllib.request.Request(url, data=data), timeout=20) as response:
        return response.read().decode()


def masked(value):
    if isinstance(value, dict):
        return {k: (v if k in ('CallSID', 'sid', 'owner_sid') and isinstance(v, str)
                    and re.fullmatch(r'CA[0-9a-f]{32}', v) else masked(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [masked(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r'(?<!\d)(?:\+?1[ .-]?)?\(?[2-9]\d{2}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\d)',
                       '[NUMBER]', value)
        value = re.sub(r'/t/[^/\s"<>]+', '/t/[SECRET]', value)
        value = re.sub(r'([?&]t=)[^&\s"<>]+', r'\1[TOKEN]', value)
        return mask_text(value)
    return value


def probe(svc, adapter, drivers, acp):
    """Synthetic session only: production TwiML/REST encoder/WSS, zero provider I/O."""
    from plugin.line.engine.machine import CallSession
    from websockets.sync.client import connect
    # A synthetic call bypasses quiet hours only for a network probe, never a dial.
    call = svc.store.create_call(task_id=None, direction=Direction.OUTBOUND,
        counterpart_e164=ALLOW[0], counterpart_masked='[NUMBER]',
        state=CallState.DIALING, mode=Mode.AGENT)
    sid = 'CA' + uuid.uuid4().hex
    svc.store.update_call(call.id, provider_sid=sid)
    call = svc.store.get_call(call.id)
    async def create():
        session = CallSession(svc, call, None)
        svc.sessions[call.id] = session
        if svc.cfg.voice.get('transport') == 'media_streams':
            svc.attach_voice(session, expected_call_sid=sid)
        return session
    session = svc.run(create())
    adapter.records[sid] = dict(sid=sid, account_sid=svc.get_secret('HERMES_LINE_TWILIO_ACCOUNT_SID'),
        direction='outbound-api', to=ALLOW[0], **{'from': svc.cfg.from_number}, status='in-progress')
    # Production _create_call/form encoding, intercepted before HTTP in the fake adapter.
    # The synthetic probe is not submitted for approval and does not consume a dial permit.
    TwilioRestAdapter._create_call(adapter, to=ALLOW[0], from_number=svc.cfg.from_number,
        answer_url=svc.url('/twilio/voice/answer/' + call.id),
        status_url=svc.url('/twilio/voice/status/' + call.id), timeout_s=30,
        time_limit_s=svc.cfg.max_call_minutes * 60)
    xml = post_answer(svc, call)
    element = ET.fromstring(xml).find('.//ConversationRelay')
    if element is None:
        element = ET.fromstring(xml).find('.//Stream')
    relay_url = element.attrib['url']
    with connect(relay_url, proxy=None, open_timeout=20, close_timeout=3) as ws:
        if not ws.ping().wait(10):
            raise Stop('public WSS ping timed out')
        # No setup or speech: keep the probe inert. A successful ping proves WSS end to end.
        result = {'https': 'ok', 'wss': '101 + ping/pong',
                  'provider_form': masked(adapter.forms[-1]), 'twiml': masked(xml)}
        if acp:
            driver = svc.run(session._in_worker(session._ensure_driver), timeout=75)
            if driver is None:
                # Preserve bounded, masked diagnostic evidence without any bearer specs.
                save(Path(drivers[-1]._cwd) / 'admission-refused.json', {
                    'session_tools_reply': masked(drivers[-1].session_tools_reply),
                    'real_provider_calls': 0, 'fake_provider_forms': len(adapter.forms),
                    'driver_error': session.driver_error})
                raise Stop('ACP session failed to attach: ' + str(session.driver_error))
            t = time.monotonic()
            reply = driver.turn('This is an offline dry harness test with no telephone call. '
                'Use tool_search to find the session MCP hermes_line_call_finish tool, then call '
                'that MCP tool with outcome done and summary "Trial dry completed". '
                'Use no other tools. Say only a short goodbye.', lambda _: None)
            elapsed = round((time.monotonic() - t) * 1000, 1)
            finish = svc.store.events(call.id, kind='finish_requested')
            steps = [{**step, 'at_ms': round((step['at'] - t) * 1000, 1)} for step in driver.steps]
            for step in steps:
                del step['at']
            search = [s for s in steps if 'tool_search' in s['title'] and s['kind'] == 'tool_call']
            later = [s for s in steps if search and s['kind'] == 'tool_call'
                     and s['at_ms'] > search[0]['at_ms']]
            from plugin.line.engine.call_tools import disallowed_session_tools, BRIDGE_TOOLS
            spec = driver._mcp_servers[0]
            headers = {h['name']: h['value'] for h in spec['headers']}
            headers['Content-Type'] = 'application/json'
            request = urllib.request.Request(spec['url'], headers=headers, data=json.dumps(
                {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}}).encode())
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=5) as response:
                mcp_names = sorted(t['name'] for t in json.load(response)['result']['tools'])
            result['acp'] = {'title': driver.title, 'profile': svc.cfg.call_profile,
                'session_tools_reply': masked(driver.session_tools_reply),
                'only_call_tools': disallowed_session_tools(driver.session_tools_reply or '',
                                                          server_names=[spec['name']]) == [],
                'session_mcp_name': spec['name'],
                'scope_projections': [masked(json.loads(line)) for line in
                    (Path(driver._cwd) / 'scope.jsonl').read_text().splitlines()],
                'bridge_tools': sorted(BRIDGE_TOOLS), 'session_mcp_tools': mcp_names,
                'start_ms': driver.start_ms,
                'turn_ms': elapsed, 'finish_tool_reached': bool(finish),
                'tool_events': steps, 'error': bool(reply.error),
                'search_to_next_tool_ms': round(later[0]['at_ms'] - search[0]['at_ms'], 1)
                    if search and later else None}
            if not finish or reply.error:
                raise Stop('dry ACP did not reach the session MCP finish tool')
    return result


class Runtime:
    def __init__(self, run_dir, cfg, adapter, get_secret, acp=False, dry=False, credit=None):
        self.run_dir, self.tunnel, self.svc, self.dns = run_dir, None, None, None
        self.store = Store(run_dir / 'home/plugin-data/hermes-line/line.db')
        self.credit = credit
        self.drivers = []
        def factory(sid):
            drv = Driver(sid, run_dir, dry, profile=cfg.call_profile) if acp else QuietDriver()
            self.drivers.append(drv)
            return drv
        voice_factories = None
        if cfg.voice.get('transport') == 'media_streams':
            if credit is None:
                raise Stop('Grok requires an active spend reservation')
            from plugin.line.voice import GrokRealtimeProvider, XaiStreamingTranscriber
            voice_factories = {'provider': lambda: GrokRealtimeProvider(get_secret, connector=credit.connector),
                               'transcriber': lambda: XaiStreamingTranscriber(get_secret, connector=credit.connector)}
        self.svc = CallService(cfg=cfg, store=self.store, adapter=adapter, driver_factory=factory,
            lock_dir=run_dir / 'home/plugin-data/hermes-line/locks', get_secret=get_secret,
            voice_factories=voice_factories)

    def __enter__(self):
        try:
            self.svc.start(port=0)
            self.tunnel = Tunnel(self.svc.server.port)
            self.svc.cfg.public_base_url = self.tunnel.start()
            self.dns = TunnelDNS(self.svc.cfg.public_base_url)
            self.dns.__enter__()
            health(self.svc.cfg.public_base_url)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        try:
            if self.svc:
                try:
                    if self.credit and self.svc.loop:
                        self.svc.run(self.credit.close_all(), timeout=5)
                finally:
                    self.svc.stop()
        finally:
            try:
                if self.tunnel:
                    self.tunnel.stop()
            finally:
                if self.dns:
                    self.dns.__exit__()
                try:
                    self.store.close()
                finally:
                    if self.credit:
                        self.credit.reconcile_shutdown()
                        self.credit.finish()


def evidence(call_id, run_dir):
    if not re.fullmatch(r'call_[a-zA-Z0-9_-]+', call_id):
        raise Stop('invalid call id')
    db = run_dir / 'home/plugin-data/hermes-line/line.db'
    store = Store(db)
    try:
        call = store.get_call(call_id)
        if not call:
            raise Stop('unknown call id')
        from plugin.line.server.desktop_api import call_view
        result = {'call': call_view(store, call), 'CallSID': call.provider_sid,
            'events': store.events(call.id), 'provider': None,
            'balance_before': None,
            'balance_after': None}
        for key in ('balance_before', 'balance_after', 'provider', 'provider_legs', 'provider_repriced', 'grok_evidence', 'hold_report'):
            path = run_dir / (key.replace('_', '-') + '.json')
            if path.exists():
                result[key] = json.loads(path.read_text())
        if 'grok_evidence' in result:
            result['grok_evidence']['connect_latency_on_human_answer'] = [e for e in result['events']
                if e['kind'] == 'voice_open' and e['data'].get('purpose') == 'conversation']
            result['grok_evidence']['first_audio_latency'] = [e for e in result['events'] if e['kind'] == 'voice_first_audio']
            result['grok_evidence']['barge_in_events'] = [e for e in result['events'] if e['kind'] == 'voice_barge_in']
            result['grok_evidence']['stt_transcript'] = [t for t in result['call']['transcript'] if t['speaker'] == 'business']
        if call.direction == Direction.INBOUND:
            events = result['events']
            outcomes = ['pass' if e['kind'] == 'owner_verified' else 'fail'
                        for e in events if e['kind'] in ('owner_verified', 'owner_pin_failed')]
            result['owner_pin_attempts'] = {'count': len(outcomes), 'results': outcomes}
            result['owner_mode_entered'] = 'owner_verified' in [e['kind'] for e in events]
            # Correlate only explicit owner-request events, never unrelated tasks.
            task_ids = list(dict.fromkeys(e['data']['task_id'] for e in events
                            if e['kind'] == 'owner_request' and e['data'].get('task_id')))
            result['tasks_created_from_call'] = task_ids
            related = [call] + [c for tid in task_ids for c in store.calls_for_task(tid)]
            result['approved_tasks'] = approved_tasks(store, call_id)
            result['related_calls'] = [{'call': call_view(store, c), 'CallSID': c.provider_sid,
                'owner_sid': c.owner_sid, 'events': store.events(c.id),
                'latencies': ringback_latencies(store.events(c.id))} for c in related]
            result['bridge_legs'] = [{'call_id': c.id, 'sid': c.owner_sid}
                                     for c in related if c.owner_sid]
        # Provider metadata was collected by call; evidence performs no API requests.
        target = REPO / 'evidence/trial' / call_id
        save(target / 'trial.json', masked(result))
        return str(target.relative_to(REPO))
    finally:
        store.close()


@contextlib.contextmanager
def monitor():
    """Let down/Ctrl-C/SIGTERM interrupt an attended probe through normal cleanup."""
    endpoint = ROOT / 'control.sock'
    endpoint.unlink(missing_ok=True)
    control = socket.socket(socket.AF_UNIX)
    control.bind(str(endpoint))
    control.listen(1)
    control.settimeout(.5)
    done = threading.Event()
    def serve():
        while not done.is_set():
            try:
                conn, _ = control.accept()
                with conn:
                    if conn.recv(32) == b'down':
                        os.kill(os.getpid(), signal.SIGINT)
                        return
            except socket.timeout:
                continue
            except OSError:
                return
    def interrupt(*_):
        raise KeyboardInterrupt
    old = signal.signal(signal.SIGTERM, interrupt)
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield
    finally:
        done.set()
        control.close()
        thread.join(timeout=2)
        endpoint.unlink(missing_ok=True)
        signal.signal(signal.SIGTERM, old)


def run_call(args, run_dir, get_secret, line):
    gateway_check(run_dir)
    adapter = TrialAdapter(get_secret, real=True, to=args.to, minutes=args.max_minutes, run_dir=run_dir)
    before = balance(adapter)
    save(run_dir / 'balance-before.json', before)
    save(ROOT / 'last-call.json', {'run_dir': str(run_dir)})
    stop = threading.Event()
    adapter.cancelled = stop
    control = socket.socket(socket.AF_UNIX)
    endpoint = ROOT / 'control.sock'
    endpoint.unlink(missing_ok=True)
    control.bind(str(endpoint))
    control.listen(1)
    control.settimeout(1)
    def serve():
        while not stop.is_set():
            try:
                conn, _ = control.accept()
                with conn:
                    if conn.recv(32) == b'down':
                        stop.set()
            except socket.timeout:
                continue
            except OSError:
                break
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    previous = {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
    call_id = None
    try:
        with Runtime(run_dir, config(line, args.max_minutes, caller_id(args.caller_id, line), args.transport), adapter, get_secret, acp=True, credit=getattr(args, '_grok', None)) as runtime:
            svc = runtime.svc
            if stop.is_set():
                raise Stop('trial shutdown requested before dialing')
            req = svc.request_call(destination=args.to, title='Attended owner trial', goal=args.task,
                mode=args.mode, allow_agent_fallback=False)
            call_id = req['call_id']
            save(ROOT / 'last-call.json', {'run_dir': str(run_dir), 'call_id': call_id})
            save(ROOT / 'calls' / (call_id + '.json'), {'run_dir': str(run_dir), 'call_id': call_id})
            out = svc.submit('approve', source='slash', task_id=req['task_id'])
            if out.get('status') == 'done' and not (out.get('result') or {}).get('ok'):
                raise Stop(masked((out.get('result') or {}).get('error') or 'call approval/dial refused'))
            emit({'command': 'call', 'call_id': call_id, 'approval_ok': bool((out.get('result') or {}).get('ok')),
                  'retry': False, 'max_minutes': args.max_minutes})
            deadline = time.monotonic() + args.max_minutes * 60 + 45
            while not stop.wait(.25):
                if svc.store.get_call(call_id).state.terminal:
                    break
                if time.monotonic() > deadline:
                    break
        # Runtime stop hangs up tracked legs before the tunnel exits.
        record_store = Store(run_dir / 'home/plugin-data/hermes-line/line.db')
        try:
            call = record_store.get_call(call_id)
            collect_provider(adapter, run_dir, [call.provider_sid, call.owner_sid])
        finally:
            record_store.close()
        try:
            save(run_dir / 'balance-after.json', balance(adapter, require_floor=False))
        except Exception as exc:
            save(run_dir / 'balance-after.json', {'unavailable': type(exc).__name__})
        emit({'evidence': evidence(call_id, run_dir), 'tunnel': 'stopped'})
    finally:
        stop.set()
        control.close()
        thread.join(timeout=2)
        endpoint.unlink(missing_ok=True)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def validate_listen(minutes, coordinator):
    if not coordinator:
        raise Stop('listen requires --i-am-the-coordinator')
    if type(minutes) is not int or not 1 <= minutes <= 10:
        raise Stop('listen minutes must be 1-10')


def recover_voice():
    if not (ROOT / 'voice-restore.json').exists():
        return False
    # This path runs under the trial lock, including down and the next start.
    os.environ['HERMES_HOME'] = str(ROOT / 'recovery-home')
    get_secret, line = credentials()
    restored = VoiceSettings(TwilioRestAdapter(get_secret), line, ROOT).restore()
    emit({'voice_settings': 'restored' if restored else 'unchanged'})
    return restored


def collect_provider(adapter, run_dir, sids):
    """Preserve first reads before waiting; one later billing read for unpriced terminal legs."""
    fields = ('sid', 'duration', 'status', 'price', 'price_unit')
    def read(sid):
        try:
            data = adapter._post('Calls/' + sid + '.json', None)
            return {**{k: data.get(k) for k in fields}, 'read_at': time.time()}
        except Exception as exc:
            return {'sid': sid, 'read_at': time.time(), 'unavailable': type(exc).__name__}
    first = [read(sid) for sid in dict.fromkeys(sids) if sid]
    if first:
        save(run_dir / 'provider.json', first[0])
    save(run_dir / 'provider-legs.json', first)
    terminal = {'completed', 'failed', 'busy', 'no-answer', 'canceled'}
    pending = [row['sid'] for row in first if 'unavailable' not in row
               and row['price'] is None and row['status'] in terminal]
    if pending:
        # A terminal response proves end preceded this observation. Waiting sixty
        # monotonic seconds after all first reads is conservative even without end_time.
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            time.sleep(min(1, max(0, deadline - time.monotonic())))
        save(run_dir / 'provider-repriced.json', [read(sid) for sid in pending])


def finish_inbound(adapter, run_dir, call_id):
    try:
        save(run_dir / 'balance-after.json', balance(adapter, require_floor=False))
    except Exception as exc:
        save(run_dir / 'balance-after.json', {'unavailable': type(exc).__name__})
    if not call_id:
        report = {'command': 'listen', 'call_id': None, 'tunnel': 'stopped', 'result': 'no accepted call'}
        save(run_dir / 'report.json', report)
        emit(report)
        return
    store = Store(run_dir / 'home/plugin-data/hermes-line/line.db')
    try:
        call = store.get_call(call_id)
        related = [call] + [c for tid in approved_tasks(store, call_id) for c in store.calls_for_task(tid)]
        collect_provider(adapter, run_dir,
            [sid for c in related for sid in (c.provider_sid, c.owner_sid)])
    finally:
        store.close()
    emit({'command': 'listen', 'call_id': call_id, 'evidence': evidence(call_id, run_dir), 'tunnel': 'stopped'})


def run_listen(args, run_dir, get_secret, line):
    validate_listen(args.minutes, args.i_am_the_coordinator)
    if getattr(args, 'transport', 'relay') != 'relay':
        raise Stop('ring-back listen requires relay')
    adapter = RingbackAdapter(get_secret, line=line, minutes=args.minutes, run_dir=run_dir)
    settings = VoiceSettings(adapter, line, ROOT)  # refuses a foreign number before any API call
    gateway_check(run_dir)
    save(run_dir / 'balance-before.json', balance(adapter))
    cfg = config(line, args.minutes, caller_id(getattr(args, 'caller_id', 'line'), line), transport=getattr(args, 'transport', 'relay'))
    cfg.forwarding_number = ALLOW[1]
    cfg.inbound_ring_owner = False  # PIN owner session first; ring-back only from approved child
    cfg.reply_budget_s = 2.5
    runtime = Runtime(run_dir, cfg, adapter, get_secret, acp=True, credit=getattr(args, '_grok', None))
    trial = InboundTrial(runtime.svc, ALLOW)
    adapter.store = runtime.store
    adapter.parent = lambda: trial.call_id
    try:
        with monitor(), runtime:
            trial.mount_status()
            try:
                settings.install(runtime.svc.url('/twilio/voice/inbound'),
                                 runtime.svc.url('/trial/status'), run_dir)
                emit({'command': 'listen', 'ready': True, 'minutes': args.minutes,
                      'line': '[the line]', 'owner': '[owner phone (Spectrum)]'})
                deadline = adapter.deadline
                while time.monotonic() < deadline and not adapter.cancelled.is_set():
                    if trial.call_id:
                        save(ROOT / 'calls' / (trial.call_id + '.json'),
                             {'run_dir': str(run_dir), 'call_id': trial.call_id})
                        if ringback_done(runtime.store, trial.call_id):
                            break
                    time.sleep(.25)
            finally:
                trial.accepting = False
                adapter.cancelled.set()
                try:
                    if trial.call_id:
                        runtime.svc.submit('hang_up', source='slash', call_id=trial.call_id)
                finally:
                    # Restore while ingress is alive, even if hangup/installation failed.
                    settings.restore()
    finally:
        # Runtime shuts down the accepted inbound/owner legs before exporting.
        if trial.call_id:
            save(ROOT / 'calls' / (trial.call_id + '.json'),
                 {'run_dir': str(run_dir), 'call_id': trial.call_id})
        finish_inbound(adapter, run_dir, trial.call_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('preflight')
    reconcile = sub.add_parser('reconcile')
    reconcile.add_argument('--i-am-the-coordinator', action='store_true')
    can = sub.add_parser('can')  # MIT-018 STEER-5: Can plays the business (Spectrum); the owner ring goes to Fi
    can.add_argument('--dry', action='store_true')
    can.add_argument('--no-quiet-hours', action='store_true')
    can.add_argument('--caller-id', choices=('line',), default='line')
    can.add_argument('--i-am-the-coordinator', action='store_true')
    hold = sub.add_parser('hold')
    hold.add_argument('--business', required=True)
    hold.add_argument('--dry', action='store_true')
    hold.add_argument('--no-quiet-hours', action='store_true')
    hold.add_argument('--caller-id', choices=('line', 'fi'), default='line')
    hold.add_argument('--i-am-the-coordinator', action='store_true')
    d = sub.add_parser('dry')
    d.add_argument('--caller-id', choices=('line', 'fi'), default='line')
    sub.add_parser('down')
    e = sub.add_parser('evidence')
    e.add_argument('call_id')
    c = sub.add_parser('call')
    c.add_argument('--to', required=True)
    c.add_argument('--task', required=True)
    c.add_argument('--mode', choices=('agent', 'owner'), default='agent')
    c.add_argument('--max-minutes', type=int, default=3)
    c.add_argument('--caller-id', choices=('line', 'fi'), default='line')
    c.add_argument('--i-am-the-coordinator', action='store_true')
    listener = sub.add_parser('listen')
    listener.add_argument('--minutes', type=int, required=True)
    listener.add_argument('--caller-id', choices=('line', 'fi'), default='line')
    listener.add_argument('--i-am-the-coordinator', action='store_true')
    for command_parser in (c, d, listener):
        command_parser.add_argument('--transport', choices=('relay', 'grok'), default='relay')
    args = parser.parse_args()
    if args.command in ('preflight', 'dry', 'call', 'listen', 'hold', 'can'):
        try:
            initialize_numbers()
        except ValueError as exc:
            raise Stop(str(exc)) from None
    if args.command == 'reconcile' and not args.i_am_the_coordinator:
        raise Stop('reconcile requires --i-am-the-coordinator')
    if args.command == 'can' and not args.dry and not args.i_am_the_coordinator:
        raise Stop('can requires --i-am-the-coordinator')
    if args.command == 'hold':
        from hold import approval
        if not args.dry and not args.i_am_the_coordinator:
            raise Stop('hold requires --i-am-the-coordinator')
        approval((sys.modules.get(__name__) or SimpleNamespace(**globals())), args.business)
    if args.command == 'listen':
        validate_listen(args.minutes, args.i_am_the_coordinator)
        if args.transport != 'relay':
            raise Stop('ring-back listen requires relay')
    if args.command == 'call':
        validate_call(args.to, args.i_am_the_coordinator, args.max_minutes)
        if not args.task.strip():
            raise Stop('task cannot be empty')
    if hashlib.sha256((REPO / 'docs/BRIEF.md').read_bytes()).hexdigest() != HASH:
        raise Stop('brief hash mismatch')
    os.umask(0o077)
    private_dir(ROOT)
    if args.command == 'down':
        try:
            with socket.socket(socket.AF_UNIX) as sock:
                sock.connect(str(ROOT / 'control.sock'))
                sock.sendall(b'down')
        except (FileNotFoundError, ConnectionRefusedError):
            pass
    with (ROOT / 'run.lock').open('a') as lock:
        deadline = time.monotonic() + (30 if args.command == 'down' else 0)
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Stop('trial still running; shutdown/restore pending, retry down')
                time.sleep(.25)
        reconcile_crashes(ROOT)  # charge interrupted Grok runs before any new admission
        if args.command in ('hold', 'can') and args.dry and any((ROOT / name).exists() for name in ('hold-recovery.json', 'voice-restore.json')):
            raise Stop('pending provider recovery; coordinator must run down before offline hold dry')
        from hold import recover as recover_hold
        if args.command == 'reconcile':
            recovered = recover_hold((sys.modules.get(__name__) or SimpleNamespace(**globals())), coordinator=True)
            emit({'command':'reconcile','status':'confirmed terminal' if recovered else 'no pending hold recovery'})
            return
        recover_hold((sys.modules.get(__name__) or SimpleNamespace(**globals())))
        recover_voice()  # fail closed if a crash left restoration pending
        if args.command == 'down':
            emit({'command': 'down', 'status': 'stopped; no pending voice restoration'})
            return
        if args.command == 'evidence':
            if not re.fullmatch(r'call_[a-zA-Z0-9_-]+', args.call_id):
                raise Stop('invalid call id')
            manifest = json.loads((ROOT / 'calls' / (args.call_id + '.json')).read_text())
            if manifest.get('call_id') != args.call_id:
                raise Stop('call id does not match recorded trial')
            emit({'evidence': evidence(args.call_id, Path(manifest['run_dir']))})
            return
        run_dir = ROOT / (args.command + '-' + uuid.uuid4().hex)
        private_dir(run_dir)
        private_dir(run_dir / 'home')
        os.environ['HERMES_HOME'] = str(run_dir / 'home')
        os.environ['HERMES_RUNTIME_DIR'] = str(run_dir / 'runtime')
        os.environ['HERMES_DISABLE_LAZY_INSTALLS'] = '1'
        credit = None
        try:
            if getattr(args, 'transport', 'relay') == 'grok':
                seconds = 5 if args.command == 'dry' else (args.max_minutes if args.command == 'call' else args.minutes) * 60
                credit = GrokRun(ROOT, run_dir, seconds, dry=args.command == 'dry')
                args._grok = credit
            execute(args, run_dir)
        except (Exception, KeyboardInterrupt) as exc:
            report_error(exc, run_dir)
            raise
        finally:
            if credit:
                credit.finish()


def execute(args, run_dir):
    if args.command in ('hold', 'can'):
        from hold import run
        return run((sys.modules.get(__name__) or SimpleNamespace(**globals())), args, run_dir)
    get_secret, line = credentials(grok=getattr(args, 'transport', 'relay') == 'grok', owner_pin=args.command == 'listen')
    if args.command == 'listen':
        run_listen(args, run_dir, get_secret, line)
        return
    if args.command == 'call':
        run_call(args, run_dir, get_secret, line)
        return
    gateway_check(run_dir)
    if args.command == 'preflight':
        funds = balance(TwilioRestAdapter(get_secret))
        save(ROOT / 'preflight-balance.json', funds)
    else:
        path = ROOT / 'preflight-balance.json'
        if not path.exists() or time.time() - path.stat().st_mtime > 1800:
            raise Stop('run preflight first (balance evidence must be under 30 minutes old)')
        funds = json.loads(path.read_text())
    adapter = TrialAdapter(get_secret)
    with monitor(), Runtime(run_dir, config(line, 3, caller_id(getattr(args, 'caller_id', 'line'), line), getattr(args, 'transport', 'relay')), adapter, get_secret, acp=args.command == 'dry', dry=True, credit=getattr(args, '_grok', None)) as rt:
        result = probe(rt.svc, adapter, rt.drivers, args.command == 'dry')
        if getattr(args, 'transport', 'relay') == 'grok':
            result['grok_connect'] = asyncio.run(dry_connect(args._grok, get_secret))
        result.update(command=args.command, balance=funds, provider='fake; zero Twilio call requests',
            dns=rt.dns.mode,
            public_origin=rt.svc.cfg.public_base_url,
            service=rt.svc.status, ingress_auth=rt.svc.auth.mode,
            allowlist=['[owner phone (Spectrum)]', '[owner phone (Fi)]'],
            owner='[owner phone (Spectrum)]', line='[NUMBER ending ' + line[-4:] + ']',
            sms_source='none', otp_browser_fill=False, recording=False, disclosure=True,
            quiet_hours=True, transport=rt.svc.cfg.voice['transport'], gateway='attach only')
    result['tunnel'] = 'stopped'
    if args.command == 'preflight':
        result['owner_pin'] = owner_pin_status()
    result['xai_ledger'] = xai_summary(ROOT)
    if getattr(args, '_grok', None):
        result['grok'] = json.loads((run_dir / 'grok-evidence.json').read_text())
    save(run_dir / 'report.json', result)
    emit(result)

if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        # Never stringify arbitrary exceptions: urllib/ACP errors can contain credentials.
        if not getattr(exc, '_trial_error_reported', False):
            report_error(exc)
        sys.exit(1)
