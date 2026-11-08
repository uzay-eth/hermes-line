"""Approved, bounded Relay hold trial. No network is used by the offline rehearsal."""
import contextlib
import json
import math
import os
import re
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from hold_recovery import CreateJournal, TERMINAL, reconcile, request_pending
from plugin.line.engine.contracts import Role
from plugin.line.engine.telephony import TwilioRestAdapter, ProviderError

D = Decimal
CAP = 2700
FALLBACK = {'business': D('.014')*D('1.5'), 'relay': D('.070')*D('1.5'),
            'owner': D('.014')*D('1.5'), 'conference': D('.0018')*D('1.5')}


def approval(api, number, path=None, now=None):
    # Validate before credentials, pricing or any provider request.
    if not re.fullmatch(r'\+1[0-9]{10}', number) or number in (api.BORA, *api.ALLOW):
        raise api.Stop('hold business destination refused')
    from plugin.line.engine import deps
    from plugin.line.policy.settings import Settings
    if not deps.check_destination(number, Settings(allowed_destinations=[number], blocked_destinations=[api.BORA], allowlist_only=True)).allowed:
        raise api.Stop('hold business policy refused')
    path = path or Path.home()/'.hermes-line/approved_destinations.json'
    if path.stat().st_mode & 0o777 != 0o600:
        raise api.Stop('destination approval file must have mode 0600')
    rows = json.loads(path.read_text())
    if not isinstance(rows, list):
        raise api.Stop('destination approvals must be a list')
    matches = [r for r in rows if isinstance(r, dict) and r.get('number') == number]
    if len(matches) != 1:
        raise api.Stop('hold needs one exact destination approval')
    row = dict(matches[0])
    try:
        expires = datetime.fromisoformat(row['expires'].replace('Z', '+00:00'))
        if expires <= (now or datetime.now(timezone.utc)):
            raise ValueError()
        if not all(isinstance(row[k], str) and row[k].strip() for k in ('label','approved_by')) or row['max_hold_minutes'] not in (20, 45):
            raise ValueError()
        if row['owner_ring_timeout_s'] != 20 or not isinstance(row['forbid'], list) or not row['forbid'] or not all(isinstance(v,str) and v.strip() for v in row['forbid']):
            raise ValueError()
        question = row['fallback_question'].strip()
        if not question.endswith('?'):
            question = 'What is the ' + question + '?'
        from plugin.line.engine.fallback import validate
        row.update(validate(question, row['owner_ring_timeout_s'], owner_mode=True, owner_number=api.ALLOW[0]))
    except (ValueError, KeyError, TypeError, AttributeError):
        raise api.Stop('hold approval expired or invalid') from None
    return row


def can_row(api, now=None):
    """MIT-018 STEER-5: the attended Can trial. Can plays the business on his own Spectrum phone (the business leg) and
    is rung as the owner on his Fi phone. No business is ever dialled; both numbers come only from the private
    personal-numbers file. Approval is the coordinator's per-run decision, valid for six hours from start."""
    from datetime import timedelta
    from plugin.line.engine.fallback import validate
    business, owner = api.ALLOW[0], api.ALLOW[1]
    question = "What is the usual nightly rate for a standard room at your hotel?"
    row = {'number': business, 'owner': owner, 'can_trial': True,
           'label': 'Can trial (Can plays the business)', 'approved_by': 'Can via Vezir, STEER-5 2026-11-07',
           'expires': ((now or datetime.now(timezone.utc)) + timedelta(hours=6)).strftime('%Y-%m-%dT%H:%M:%SZ'),
           'max_hold_minutes': 10, 'owner_ring_timeout_s': 20, 'fallback_question': question,
           'forbid': ['make, change or cancel a booking', 'payment or card details',
                      'invent identity or Honors number', 'accept callback'],
           'intro_names': [], 'ivr_category': 'reservations'}
    row.update(validate(question, 20, owner_mode=True, owner_number=owner))
    return row


class Events:
    def __init__(self, api, root):
        self.api, self.root = api, root
        self.lock = threading.Lock()
        self.sent = set()
        self.times = {}
        self.call_id = None
        self.records = []

    def emit(self, event, *, once=True, **fields):
        with self.lock:
            if once and event in self.sent:
                return
            row = dict(event=event, at=time.time(), **fields)
            line = json.dumps(row, sort_keys=True)
            with (self.root/'live-events.jsonl').open('a') as out:
                os.chmod(out.name, 0o600)
                out.write(line+'\n'); out.flush(); os.fsync(out.fileno())
            print(line, flush=True)
            live = os.environ.get('HL_LIVE_CALL_FILE')
            if live and event in ('business_dialing', 'human_detected'):
                stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                text = ('CALL-START ' + stamp + ' ' + os.environ.get('HL_LIVE_CALL_LABEL', 'Hilton Reservations')
                        + ' dialed (' + str(fields.get('sid')) + ')'
                        if event == 'business_dialing' else
                        'HUMAN-ON-LINE ' + stamp + ' person detected; ringing owner phone next')
                with open(live, 'a') as out:
                    out.write(text + '\n'); out.flush(); os.fsync(out.fileno())
            self.records.append(row)
            self.sent.add(event)
            self.times[event] = time.monotonic()

    def attach(self, store):
        original = store.log_event
        def logged(call_id, task_id, kind, data=None):
            original(call_id, task_id, kind, data)
            if call_id != self.call_id:
                return
            data = data or {}
            if kind == 'state':
                name = {'human_detected':'human_detected', 'owner_connected':'owner_bridged'}.get(data.get('to'))
                if name:
                    self.emit(name)
            elif kind == 'new_party_question':
                self.emit('new_party_question', once=False, count=data.get('count'))
            elif kind == 'ivr_action':
                self.emit('assistant_phrase', once=False, phrase_id=data.get('phrase_id'),
                          detection_state=data.get('state'))
            elif kind == 'fallback_question_asked':
                self.emit('fallback_question')
        store.log_event = logged  # synchronous event observation, no engine policy override


class Budget:
    def __init__(self, rates, read, clock=time.monotonic):
        self.rates, self.read, self.clock = rates, read, clock
        self.start = None
        self.initial = self.latest = None
        self.polled = None
        self.polls = []
        self.lock = threading.Lock()

    @property
    def per_minute(self):
        # Charge both legs, Relay and TWO conference participants for the entire
        # elapsed time even before ring/bridge: a deliberately conservative floor.
        return self.rates['business'] + self.rates['relay'] + self.rates['owner'] + 2*self.rates['conference']

    def estimate(self, seconds):
        return self.per_minute * D(math.ceil(max(0, seconds)/60))

    def check(self, force=False):
        with self.lock:
            return self._check(force)

    def _check(self, force=False):
        now = self.clock()
        if self.polled is None or force or now-self.polled >= 60:
            value = D(self.read()['usd'])
            if not value.is_finite():
                raise ProviderError('hold balance unavailable')
            if self.initial is None:
                self.initial = value
                # Full envelope, plus 90s for polling/cleanup, leaves original $5 reserve.
                if value-self.estimate(CAP+90) < D('5'):
                    raise ProviderError('hold worst-case envelope would consume reserve')
            self.latest, self.polled = value, self.clock()
            self.polls.append({'at':time.time(), 'usd':str(value)})
        elapsed = 0 if self.start is None else self.clock()-self.start
        projected = min(self.initial-self.estimate(elapsed+90),
                        self.latest-self.estimate(self.clock()-self.polled+90))
        if projected <= D('4'):
            raise ProviderError('hold projected balance reached $4.00')
        return projected


class HoldAdapter(TwilioRestAdapter):
    def __init__(self, api, get_secret, row, line, root, events, *, real, clock=time.monotonic):
        super().__init__(get_secret)
        self.api, self.row, self.line, self.root, self.events = api, row, line, root, events
        self.real = self.live = real
        self.clock = clock
        self.forms, self.records, self.sids = [], {}, []
        self.lock = threading.RLock()
        self.drained = threading.Condition(self.lock)
        self.create_lock = threading.Lock()
        self.active = 0
        self.journal = CreateJournal(root, live=real)
        self.context = threading.local()
        self.attempts = set()
        self.task_id = self.call_id = None
        self.start = None
        self.started_at = None
        self._stopped = False
        self.stop_requested = threading.Event()
        self.rates = dict(FALLBACK)
        self.rate_basis = 'S2 planning rates x1.5; no pricing read in dry'
        self.budget = Budget(self.rates, lambda: api.balance(self, require_floor=False), clock)

    def pricing(self):
        # Only executed by the coordinator's live command. An inference about
        # Relay/conference account rates is never made from Voice number pricing.
        try:
            _, token = self._auth()
            req = urllib.request.Request('https://pricing.twilio.com/v2/Voice/Numbers/'+self.row['number'],
                headers={'Authorization':'Basic '+token})
            with self._open(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            prices = [D(p['current_price']) for p in data['outbound_call_prices']]
            if data['price_unit'].upper() != 'USD' or not prices or any(not p.is_finite() or p < 0 for p in prices):
                raise ValueError()
            self.rates['business'] = max(FALLBACK['business'], max(prices)*D('1.5'))
            self.rate_basis = 'Voice number Pricing API x1.5 (business); S2 x1.5 (owner/Relay/conference)'
        except Exception:
            self.rate_basis = 'Pricing API unavailable; S2 planning rates x1.5'

    def _post(self, path, params):
        if self.real:
            return super()._post(path, params)
        if path.startswith('Conferences.json?'):
            return {'conferences':[{'sid':'CF'+'0'*32,'friendly_name':'hl-'+self.call_id}]}
        if path == 'Conferences/'+'CF'+'0'*32+'.json':
            self.conference_ended = True
            return {'status':'completed'}
        if path == 'Balance.json':
            return {'balance':'20.00', 'currency':'USD'}
        return self.api.TrialAdapter._post(self, path, params)

    def place_call(self, permit, **kw):
        self.context.permit = permit
        try:
            return super().place_call(permit, **kw)
        finally:
            self.context.permit = None

    @property
    def stopped(self):
        with self.lock:
            return self._stopped or self.stop_requested.is_set()

    @stopped.setter
    def stopped(self, value):
        if value:
            self.stop_requested.set()
        with self.lock:
            self._stopped = bool(value)
            self.drained.notify_all()

    def check_dispatch(self):
        if self._stopped or self.stop_requested.is_set():
            raise ProviderError('hold stopped before dispatch commitment')
        if datetime.now(timezone.utc) >= datetime.fromisoformat(self.row['expires'].replace('Z','+00:00')):
            raise ProviderError('hold approval expired before dispatch')
        remaining = int(CAP-(self.clock()-self.start))
        if remaining < 1:
            raise ProviderError('hold global deadline reached before dispatch')
        return remaining

    def _create_call(self, **kw):
        with self.lock:
            if self._stopped or self.stop_requested.is_set():
                raise ProviderError('hold stopped')
            self.active += 1  # includes blocking preparation and HTTP/SID persistence
        try:
            with self.create_lock:
                return self._dispatch(kw)
        except BaseException:
            self.stopped = True
            raise
        finally:
            with self.lock:
                self.active -= 1
                self.drained.notify_all()

    def _dispatch(self, kw):
        p = getattr(self.context, 'permit', None)
        with self.lock:
            if (self._stopped or self.stop_requested.is_set() or not p or p.task_id != self.task_id or p.call_id != self.call_id
                    or kw['to'] in (self.line, self.api.BORA)):
                raise ProviderError('hold create outside approved task')
            if p.role == Role.BUSINESS:
                valid = kw['to'] == self.row['number'] and not self.attempts
            elif p.role == Role.OWNER:
                valid = kw['to'] == self.row.get('owner', self.api.ALLOW[0]) and Role.BUSINESS in self.attempts
            else:
                valid = False
            if not valid or p.role in self.attempts:
                raise ProviderError('hold role/destination/retry refused')
            self.attempts.add(p.role)
            if self.start is None:
                self.started_at, self.start = time.time(), self.clock()
                self.budget.start = self.start
        if self.real and self.reapprove() != self.row:
            raise ProviderError('hold approval changed')
        with self.lock:self.check_dispatch()
        self.api.save(self.root/'hold-journal.json', {'attempts':[r.value for r in self.attempts],
            'started_at':self.started_at, 'cap_s':CAP, 'retry':False})
        with self.lock:self.check_dispatch()
        self.budget.check(force=True)  # stop may run while this blocks
        with self.lock:self.check_dispatch()
        if self.real and self.reapprove() != self.row:
            raise ProviderError('hold approval changed after balance')
        # Stop and final dispatch commitment share this lock. No blocking network
        # step intervenes between this commitment and the tracked HTTP operation.
        with self.lock:
            remaining = self.check_dispatch()
            if p.role == Role.OWNER:
                if remaining < self.row['owner_ring_timeout_s']+5:
                    raise ProviderError('hold global deadline too close')
                if 'human_detected' not in self.events.sent:
                    raise ProviderError('owner create before HUMAN event refused')
                kw['timeout_s'] = self.row['owner_ring_timeout_s']
            record = self.journal.intent(p.role,kw['to'],kw['from_number'],self.task_id,self.call_id,
                                         time.time(),self.started_at+CAP)
            try:
                kw['time_limit_s'] = self.check_dispatch()  # fsync can cross expiry
            except BaseException:
                record.update(state='not_dispatched',in_flight=False)
                self.journal.persist()
                raise
            record['committed'] = True
            # intent is already durable; an interrupted commit remains unresolved.
        sid = None
        try:
            sid = super()._create_call(**kw)
            with self.lock:
                self.sids.append(sid)
                self.journal.known(record,sid)
                late = self._stopped or self.stop_requested.is_set()
            if late:
                self.end_confirmed(sid)
                raise ProviderError('hold create returned after stop; leg ended')
            name = 'business_dialing' if p.role == Role.BUSINESS else 'owner_ring'
            self.events.emit(name,sid='[SID ending '+sid[-6:]+']')
            return sid
        except BaseException:
            self.stopped = True
            # Includes failure to persist a returned SID. Leave the durable intent
            # recoverable even if immediate hangup/persistence also fails.
            if sid:
                try:self.end_confirmed(sid)
                except Exception:pass
            raise
        finally:
            with self.lock:
                record['in_flight'] = False
                self.journal.persist()  # only then may a different startup clear this attempt

    def reapprove(self):
        # The Can trial's row is the coordinator's per-run approval (check_dispatch enforces its expiry); a business
        # row is re-read from the approval file before every create.
        return self.row if self.row.get('can_trial') else approval(self.api, self.row['number'])

    def end_confirmed(self, sid):
        self.end_call(sid)
        check = self.fetch_call(sid)
        if not check or check.get('sid') != sid or check.get('status') not in TERMINAL:
            raise ProviderError('hold leg termination unconfirmed')
        with self.lock:
            self.journal.ended(sid)

    def tick(self):
        if datetime.now(timezone.utc) >= datetime.fromisoformat(self.row['expires'].replace('Z','+00:00')):
            self.stopped = True
            raise ProviderError('hold approval expired')
        if self.stopped:
            raise ProviderError('hold stopped')
        if self.start is not None and self.clock()-self.start >= CAP:
            self.stopped = True
            raise ProviderError('hold global deadline reached')
        try:
            return self.budget.check()
        except BaseException:
            self.stopped = True
            raise

    def cleanup(self, conference, *, coordinator=False):
        self.stopped = True
        failures = []
        with self.drained:
            if not self.drained.wait_for(lambda: self.active == 0, timeout=15):
                failures.append({'creates_in_flight':self.active, 'status':'unresolved_cleanup'})
        for sid in list(dict.fromkeys(self.sids+self.journal.sids)):
            try:
                self.end_confirmed(sid)
            except Exception as exc:
                failures.append({'sid':sid, 'error':type(exc).__name__})
        # End-on-exit already closes the conference when both tracked legs end.
        # Explicitly complete the exact trial conference as well; never mutate others.
        if conference:
            from urllib.parse import urlencode
            try:
                found = self._post('Conferences.json?'+urlencode({'FriendlyName':conference,'Status':'in-progress'}), None)
                for row in found.get('conferences', []):
                    if row.get('friendly_name') != conference or not re.fullmatch(r'CF[0-9a-f]{32}', row.get('sid','')):
                        raise ProviderError('foreign conference response refused')
                    self._post('Conferences/'+row['sid']+'.json', [('Status','completed')])
            except Exception as exc:
                failures.append({'conference':conference, 'error':type(exc).__name__})
        unresolved = [r for r in self.journal.entries if r['state'] not in ('ended','not_dispatched') or request_pending(r)]
        if unresolved:
            failures.append({'status':'unresolved_cleanup', 'roles':[r['role'] for r in unresolved],
                'check':'Check To, From, attempt_at and StartTime window in '+str(self.journal.pending)+
                        '; verify every matching Twilio Call SID is terminal; retry down.'})
        with self.lock:
            if not failures and self.active == 0:
                try:self.journal.clear(coordinator=coordinator)
                except ProviderError:
                    failures.append({'status':'unresolved_cleanup','check':'Recovery journal still has unconfirmed or in-flight legs; inspect '+str(self.journal.pending)+' and retry down.'})
        self.api.save(self.root/'hold-cleanup.json', {'failures':failures, 'known_sids':self.sids})
        return failures


def task_args(api, row):
    hotel = row['label'].startswith('Hilton') or bool(row.get('can_trial'))
    guidance = (api.REPO/('fixtures/hold-sim/'+('hotel' if hotel else 'airline')+'-agent-guidance.txt')).read_text()
    args = dict(destination=row['number'], title=row['label'], mode='owner',
        goal=guidance+'\nApproved decision A overrides the older no-fallback instruction: after the owner timeout, '
             'ask only the engine-enforced fallback question, then end. Restrictions: no callback, no payment, '
             +('no booking, change or cancellation' if hotel else 'no flight change')+'; never invent identity or booking.\nApproval restrictions: '+', '.join(row['forbid']),
        allow_agent_fallback=False, owner_ring_timeout_s=row['owner_ring_timeout_s'],
        fallback_question=row['fallback_question'], intro_names=row.get('intro_names', []))
    if 'ivr_category' in row:
        args['ivr_category'] = row['ivr_category']
    return args


def metrics(store, call_id, dry):
    events = store.events(call_id)
    def state(name):
        return next((e['ts'] for e in events if e['kind']=='state' and e['data'].get('to')==name), None)
    menu, hold, human, bridge = [state(s) for s in ('ivr','on_hold','human_detected','owner_connected')]
    def delta(a,b):return None if a is None or b is None else max(0,b-a)
    prior = [t['ts'] for t in store.transcript(call_id, limit=1000)
             if t['speaker']=='business' and human is not None and t['ts'] <= human]
    return {'menu_s':delta(menu,hold), 'hold_s':delta(hold,human),
        'transcript_to_human_s':delta(max(prior) if prior else None,human),
        'bridge_s':delta(human,bridge), 'false_human_count':0 if dry else None,
        'false_human_basis':'scripted ground truth' if dry else 'requires independent operator annotation',
        'human_assumed_count':sum(e['kind']=='human_assumed' for e in events)}


def finish(api, adapter, store, call_id, root, outcome, dry):
    call = store.get_call(call_id) if call_id else None
    failures = adapter.cleanup(call.conference if call else None)
    unresolved = any(f.get('status')=='unresolved_cleanup' for f in failures)
    effective = 'unresolved_cleanup' if unresolved else 'cleanup_failed' if failures else outcome
    report = {'dry':dry, 'outcome':effective, 'call_outcome':outcome, 'cleanup_failures':failures,
        'rates':{k:str(v) for k,v in adapter.rates.items()}, 'rate_basis':adapter.rate_basis,
        'balance_reads':adapter.budget.polls,
        'conservative_cost_usd':str(adapter.budget.estimate(0 if adapter.start is None else adapter.clock()-adapter.start)),
        'cost_basis':'conservative estimate, separate from Twilio billing',
        'metrics':metrics(store, call_id, dry) if call else None}
    api.save(root/'hold-report.json', report)
    adapter.events.emit('ended', outcome=effective)
    if call:
        if not dry:
            api.collect_provider(adapter, root, [call.provider_sid, call.owner_sid])
        api.save(root/'hold-transcript.json', api.masked(store.transcript(call_id,limit=1000)))
        api.save(root/'hold-engine-events.json', api.masked(store.events(call_id)))
    return report


def dry_run(api, row, root):
    # This is a local real-engine rehearsal, no tunnel, gateway, staging credentials,
    # pricing requests or Twilio HTTP. The fake ACP driver only follows the fixture menu.
    sys.path.insert(0, str(api.REPO/'tests/l1'))
    from harness import Line, SECRETS, wait_for
    from plugin.line.engine import fallback
    from unittest.mock import patch
    def reply(driver, text):
        if '[phone menu]' in text:
            driver.tool('press_digits','2')
        return ['WAIT']
    data_dir = root/'home/plugin-data/hermes-line'
    owner = row.get('owner', api.ALLOW[0])
    line = Line(start=False, store_dir=data_dir, owner_name='Can', owner_number=owner,
        max_call_minutes=45, reply=reply, policy={'allowed_destinations':[row['number'], owner],
            'blocked_destinations':[api.BORA]})
    events = Events(api,root)
    adapter = HoldAdapter(api, SECRETS.get, row, line.cfg.line_number,root,events,real=False)
    line.svc.adapter = adapter
    events.attach(line.store)
    req = None
    outcome = 'failed'
    try:
        line.svc.start(port=0)
        req = line.svc.request_call(**task_args(api,row))
        adapter.task_id, adapter.call_id = req['task_id'], req['call_id']
        events.call_id = req['call_id']
        assert line.approve(req['task_id'])['result']['ok']
        wait_for(lambda: line.store.get_call(req['call_id']).provider_sid)
        session = line.session(req['call_id'])
        # Only the engine watchdog is accelerated; provider Timeout remains the approved 20s.
        session.options['owner_ring_timeout_s'] = .15
        relay = line.connect(req, {'sid':line.store.get_call(req['call_id']).provider_sid, 'to':row['number']})
        with patch.object(fallback,'QUIET_AFTER_ANSWER_S',.05), patch.object(fallback,'ANSWER_MAX_S',2):
            names = row.get('intro_names', [])
            greeting = 'Welcome to '+names[0]+'.' if names else 'Thank you for calling.'
            relay.prompt(greeting+' For baggage, press 2.')
            wait_for(lambda: any(e['kind']=='dtmf_sent' for e in line.store.events(req['call_id'])))
            relay.prompt('All representatives are busy. Please stay on the line. Your call is important to us.')
            line.wait_state(req['call_id'],'on_hold')
            assert 'human_detected' not in events.sent
            relay.prompt('Hi, this is Morgan with Delta, how can I help?')
            relay.wait_text('live person?')
            assert 'new_party_question' in events.sent and len(adapter.forms) == 1
            relay.prompt('Yes, speaking.')
            relay.wait_text(row['fallback_question'])
            relay.prompt('The checked bag fee is thirty five dollars.')
            line.wait_state(req['call_id'],'ended')
            line.wait_task(req['task_id'],'done')
        assert [r['event'] for r in events.records][:5] == ['business_dialing','new_party_question','human_detected','owner_ring','fallback_question']
        assert len(adapter.forms)==2 and adapter.forms[1]['Timeout']=='20'
        api.save(root/'hold-dry-forms.json', api.masked(adapter.forms))
        outcome='fallback_question_answered'
    finally:
        line.svc.stop()
        report = finish(api,adapter,line.store,req['call_id'] if req else None,root,outcome,True)
        line.store.close()
    api.emit({'command':'hold','dry':True,'report':str(root/'hold-report.json'),
              'provider':'fake; no external network','engine_timeout_s':.15,'provider_owner_timeout_s':20,
              'fallback_question':row['fallback_question'], 'outcome':report['outcome']})
    return report


@contextlib.contextmanager
def dispatch_window(adapter):
    try:
        yield
    finally:
        adapter.stopped = True  # before service teardown, including an in-flight ACP setup


def run(api, args, root):
    global CAP
    row = can_row(api) if getattr(args, 'command', None) == 'can' else approval(api,args.business)
    CAP = row['max_hold_minutes']*60  # trial copy: the approval's own hold cap (Hilton 20 min)
    if args.dry:
        return dry_run(api,row,root)
    getter, line = api.credentials()
    if row['number'] == line:
        raise api.Stop('plugin line cannot be a business destination')
    api.gateway_check(root)
    cfg = api.config(line,45,api.caller_id(args.caller_id,line))
    cfg.owner_number = row.get('owner', api.ALLOW[0])  # the Can trial rings the Fi phone; business rows ring Spectrum
    cfg.policy['allowed_destinations'] = [row['number'],cfg.owner_number]
    if getattr(args, 'no_quiet_hours', False):
        cfg.policy['quiet_hours_enabled'] = False  # this one trial run only; plugin default stays on
    events = Events(api,root)
    adapter = HoldAdapter(api,getter,row,line,root,events,real=True)
    adapter.pricing()
    adapter.budget.check(force=True)
    api.save(root/'balance-before.json', {'usd':str(adapter.budget.initial),'at':time.time()})
    call_id = None
    outcome = 'failed'
    runtime = api.Runtime(root,cfg,adapter,getter,acp=True)
    events.attach(runtime.store)
    try:
        with api.monitor(), runtime, dispatch_window(adapter):
            req = runtime.svc.request_call(**task_args(api,row))
            adapter.task_id, adapter.call_id = req['task_id'],req['call_id']
            events.call_id = call_id = req['call_id']
            api.save(api.ROOT/'calls'/(call_id+'.json'), {'run_dir':str(root),'call_id':call_id})
            result = runtime.svc.submit('approve',source='slash',task_id=req['task_id'],wait_s=90)
            if not (result.get('result') or {}).get('ok'):
                raise api.Stop(api.masked((result.get('result') or {}).get('error') or 'hold approval/dial refused'))
            while not runtime.store.get_call(call_id).state.terminal:
                adapter.tick()
                ev = runtime.store.events(call_id)
                menu = next((e['ts'] for e in ev if e['kind']=='state' and e['data'].get('to')=='ivr'),None)
                queued = any(e['kind']=='state' and e['data'].get('to')=='on_hold' for e in ev)
                if menu and not queued and time.time()-menu > 300:
                    raise api.Stop('hold menu exceeded five minutes')
                time.sleep(.2)
            outcome = runtime.store.get_call(call_id).end_reason or 'ended'
    except BaseException as exc:
        outcome = str(exc) if isinstance(exc, (api.Stop, ProviderError)) else type(exc).__name__
        raise
    finally:
        # Runtime has joined service teardown before this explicit provider cleanup.
        store = api.Store(root/'home/plugin-data/hermes-line/line.db')
        try:
            report = finish(api,adapter,store,call_id,root,outcome,False)
        finally:
            store.close()
        if call_id:
            api.emit({'evidence':api.evidence(call_id,root)})
        if report['cleanup_failures']:
            raise api.Stop('hold cleanup unresolved; check hold-recovery.json To/From/DateCreated >= attempt_at - 2s (no upper bound), creator PID and matching SIDs; retry down')


def recover(api, *, coordinator=False):
    pending = api.ROOT/'hold-recovery.json'
    if not pending.exists():
        return False
    row = json.loads(pending.read_text())
    root = Path(row['run_dir'])
    if root.parent != api.ROOT or not re.fullmatch(r'call_[0-9a-f]+',row['call_id']):
        raise api.Stop('invalid hold recovery journal')
    if not all(re.fullmatch(r'CA[0-9a-f]{32}',sid) for sid in row['sids']):
        raise api.Stop('invalid hold recovery SID')
    os.environ['HERMES_HOME'] = str(api.ROOT/'hold-recovery-home')
    getter,line = api.credentials()
    adapter = HoldAdapter(api,getter,{},line,root,Events(api,root),real=True)
    adapter.sids = row['sids']
    adapter.call_id = row['call_id']
    adapter.journal.live = True
    adapter.journal.sids = list(row['sids'])
    adapter.journal.entries = row.get('creates',[])
    adapter.journal.ended_sids = row.get('ended_sids',[])
    adapter.journal.call_id = row['call_id']
    adapter.journal.task_id = row.get('task_id')
    try:
        reconcile(adapter, coordinator=coordinator)
    except Exception:
        # Known legs must still be ended; unresolved attempts remain startup-visible.
        adapter.cleanup('hl-'+row['call_id'], coordinator=coordinator)
        raise api.Stop('unresolved hold create: inspect To/From/DateCreated >= attempt_at - 2s (no upper bound) and live matches in '+str(pending)+'; verify candidate final states; coordinator must run reconcile --i-am-the-coordinator') from None
    if adapter.cleanup('hl-'+row['call_id'], coordinator=coordinator):
        raise api.Stop('unresolved hold cleanup: inspect '+str(pending)+' To/From/DateCreated >= attempt_at - 2s (no upper bound), in-flight creator PID and matching SIDs; retry down')
    api.emit({'hold_recovery':'completed'})
    return True
