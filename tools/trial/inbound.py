"""Private voice-settings journal and one-call ingress guard; no network at import."""
import asyncio
import json
import os
from pathlib import Path
import re
from urllib.parse import urlencode, parse_qsl

from personal_numbers import load_line_number

# None: ~/.hermes-line/twilio_line_number, the same configured line credentials() in run.py reads. Tests override it.
LINE_NUMBER_PATH = None

# Only these fields are modified/restored. SMS and fallback settings are never posted.
VOICE_FIELDS = {'VoiceUrl': 'voice_url', 'VoiceMethod': 'voice_method',
                'StatusCallback': 'status_callback', 'StatusCallbackMethod': 'status_callback_method',
                'VoiceApplicationSid': 'voice_application_sid'}


def durable_save(path, value):
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temp = path.with_suffix('.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


class VoiceSettings:
    """Write-ahead recovery journal. Call only while holding the global trial lock."""
    def __init__(self, adapter, line, root):
        # Check before even the filtered provider lookup, including crash recovery: only the line the user
        # configured (LINE_NUMBER_PATH, the file credentials() in run.py reads). A missing, unreadable or
        # invalid configured line refuses.
        try:
            configured = load_line_number(LINE_NUMBER_PATH)
        except ValueError:
            raise ValueError('the configured plugin line is missing or unreadable') from None
        if not isinstance(line, str) or line != configured or line == '+17755550172':
            raise ValueError('only the configured plugin line may be configured')
        self.adapter, self.line, self.root = adapter, line, Path(root)
        self.pending = self.root / 'voice-restore.json'

    def lookup(self):
        result = self.adapter._post('IncomingPhoneNumbers.json?' + urlencode({'PhoneNumber': self.line}), None)
        rows = result.get('incoming_phone_numbers', [])
        if len(rows) != 1 or rows[0].get('phone_number') != self.line:
            raise RuntimeError('exact plugin number lookup failed')
        row = rows[0]
        if not re.fullmatch(r'PN[0-9a-fA-F]{32}', row.get('sid', '')):
            raise RuntimeError('invalid plugin number resource')
        return row

    def restore(self):
        if not self.pending.exists():
            return False
        state = json.loads(self.pending.read_text())
        if state['line'] != self.line or set(state['previous']) != set(VOICE_FIELDS):
            raise RuntimeError('restore journal does not match the plugin line')
        if not Path(state['backup']).resolve().is_relative_to(self.root.resolve()):
            raise RuntimeError('restore backup is outside the trial root')
        # Resolve the exact number again. Never follow a foreign SID from a journal.
        row = self.lookup()
        if row['sid'] != state['sid']:
            raise RuntimeError('restore resource mismatch')
        values = state['previous']
        if not all(isinstance(v, str) for v in values.values()):
            raise RuntimeError('invalid restore settings')
        self.adapter._post('IncomingPhoneNumbers/' + row['sid'] + '.json', list(values.items()))
        verified = self.lookup()
        if any((verified.get(name) or '') != values[field] for field, name in VOICE_FIELDS.items()):
            raise RuntimeError('voice restoration could not be verified')
        # If the response is lost, the journal stays pending and the same restore
        # is safely retried next start. Keep an immutable copy in the run folder.
        durable_save(Path(state['backup']).with_name('voice-restored.json'), {'restored': True})
        self.pending.unlink()
        directory = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return True

    def install(self, voice_url, status_url, run_dir):
        if self.pending.exists():
            raise RuntimeError('pending restoration must complete before installing new URLs')
        row = self.lookup()
        previous = {}
        for field, name in VOICE_FIELDS.items():
            if name not in row or (row[name] is not None and not isinstance(row[name], str)):
                raise RuntimeError('voice settings snapshot incomplete')
            previous[field] = row[name] or ''
        backup = Path(run_dir) / 'voice-before.json'
        state = {'line': self.line, 'sid': row['sid'], 'previous': previous, 'backup': str(backup)}
        durable_save(backup, state)
        durable_save(self.pending, state)  # durable BEFORE the first mutation (even ambiguous failures)
        self.adapter._post('IncomingPhoneNumbers/' + row['sid'] + '.json', [
            ('VoiceApplicationSid', ''), ('VoiceUrl', voice_url), ('VoiceMethod', 'POST'),
            ('StatusCallback', status_url), ('StatusCallbackMethod', 'POST')])


class InboundTrial:
    """Narrow the existing plugin admission; keep its auth/REST and state machine."""
    def __init__(self, svc, allow):
        self.svc, self.allow = svc, tuple(allow)
        self.call_id = self.sid = None
        self.accepting = True
        self.lock = asyncio.Lock()
        original = svc.on_inbound
        async def admit(params):
            from plugin.line.server import twiml
            async with self.lock:
                sid, caller = params.get('CallSid', ''), params.get('From', '')
                if (not self.accepting or caller not in self.allow
                        or (self.sid is not None and self.sid != sid)):
                    return twiml.response(twiml.reject('rejected'))
                body = await original(params)  # original auth precedes this hook in ingress
                call = svc.store.find_call_by_sid(sid)
                if call is not None and call.direction.value == 'inbound':
                    self.sid, self.call_id = sid, call.id
                return body
        svc.on_inbound = admit

    def mount_status(self):
        from starlette.routing import Route
        from starlette.responses import Response
        app = self.svc.server._server.config.app
        original = next(r.endpoint for r in app.routes
                        if r.path == '/t/{secret}/twilio/voice/status/{call_id}')
        async def status(request):
            body = b''
            async for chunk in request.stream():
                body += chunk
                if len(body) > 64 * 1024:
                    return Response(status_code=413)
            request._body = body  # replay the bounded body to the production verifier
            try:
                params = dict(parse_qsl(body.decode(), keep_blank_values=True))
            except (ValueError, UnicodeDecodeError):
                return Response(status_code=400)
            # Only map this trial's accepted business SID, never its owner leg.
            request.path_params['call_id'] = self.call_id if params.get('CallSid') == self.sid else '_unknown_'
            return await original(request)  # account/path-secret/REST/status checks stay in place
        app.routes.append(Route('/t/{secret}/trial/status', status, methods=['POST']))
