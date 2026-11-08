"""Durable hold-create intent and narrowly scoped, fail-closed reconciliation."""
import contextlib
import fcntl
import json
import math
import os
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode
from inbound import durable_save
from plugin.line.engine.telephony import ProviderError

TERMINAL = {'completed', 'failed', 'busy', 'no-answer', 'canceled'}
SID = re.compile(r'CA[0-9a-f]{32}')


def stamp(value):
    if isinstance(value, (int, float)):
        return value
    try:
        return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
    except (ValueError, TypeError, AttributeError):
        return parsedate_to_datetime(value).timestamp()


def utc(value):
    return datetime.fromtimestamp(value, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def request_pending(row):
    if not row.get('in_flight'):
        return False
    try:
        os.kill(row['creator_pid'], 0)  # existence check only; never sends a signal
    except ProcessLookupError:
        return False
    except (PermissionError, KeyError, TypeError):
        return True  # cannot establish that the original creator is quiescent
    return True


class CreateJournal:
    def __init__(self, root, *, live):
        self.root, self.live = root, live
        self.path = root/'hold-create-journal.json'
        self.pending = root.parent/'hold-recovery.json'
        self.entries = []
        self.task_id = self.call_id = None
        self.sids = []
        self.ended_sids = []

    @contextlib.contextmanager
    def file_lock(self):
        self.root.parent.mkdir(parents=True,exist_ok=True)
        fd = os.open(self.root.parent/'hold-recovery.lock',os.O_CREAT|os.O_RDWR,0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX)
            yield
        finally:os.close(fd)

    def persist(self):
        with self.file_lock():
            if self.live and self.pending.exists():
                old = json.loads(self.pending.read_text())
                if old['run_dir'] != str(self.root):
                    raise ProviderError('another hold run owns the recovery marker')
                self.sids = list(dict.fromkeys(self.sids+old['sids']))
                self.ended_sids = list(dict.fromkeys(self.ended_sids+old.get('ended_sids',[])))
                keys = {(r['role'],r['call_id'],r['attempt_at']) for r in self.entries}
                self.entries.extend(r for r in old.get('creates',[])
                    if (r['role'],r['call_id'],r['attempt_at']) not in keys)
                for row in self.entries:
                    prior = next((r for r in old.get('creates',[]) if
                        (r['role'],r['call_id'],r['attempt_at']) == (row['role'],row['call_id'],row['attempt_at'])),None)
                    if prior:
                        row['matches'] = list(dict.fromkeys(row.get('matches',[])+prior.get('matches',[])))
                        # Once a creator has durably finished, a stale reconciler
                        # must not resurrect its in-flight status.
                        row['in_flight'] = row.get('in_flight',False) and prior.get('in_flight',False)
            data = {'version':2, 'run_dir':str(self.root), 'task_id':self.task_id,
                    'call_id':self.call_id, 'sids':self.sids, 'ended_sids':self.ended_sids, 'creates':self.entries}
            durable_save(self.path,data)
            if self.live:durable_save(self.pending,data)

    def intent(self, role, to, from_, task_id, call_id, at, deadline):
        self.task_id, self.call_id = task_id, call_id
        row = {'role':role.value, 'to':to, 'from':from_, 'task_id':task_id, 'call_id':call_id,
               'attempt_at':at, 'absolute_deadline':deadline, 'state':'unresolved', 'sid':None,
               'date_created_floor':at-2, 'in_flight':True, 'creator_pid':os.getpid()}
        self.entries.append(row)
        self.persist()  # must complete before commitment/Calls POST
        return row

    def known(self, row, sid):
        if sid not in self.sids:
            self.sids.append(sid)
        row.update(sid=sid, state='known')
        self.persist()

    def ended(self, sid):
        if sid not in self.ended_sids:self.ended_sids.append(sid)
        for row in self.entries:
            if row.get('sid') == sid:
                row['state'] = 'ended'
        self.persist()

    def clear(self, *, coordinator=False):
        with self.file_lock():
            if self.live and self.pending.exists():
                latest = json.loads(self.pending.read_text())
                if latest['run_dir'] != str(self.root):
                    raise ProviderError('another hold run owns the recovery marker')
                entries, sids, ended = latest.get('creates',[]), latest['sids'], latest.get('ended_sids',[])
            else:
                entries, sids, ended = self.entries,self.sids,self.ended_sids
            if (any(row['state'] not in ('ended','not_dispatched') or request_pending(row)
                    or (not row.get('sid') and row['state'] != 'not_dispatched' and not coordinator) for row in entries)
                    or not set(sids).issubset(ended)):
                raise ProviderError('unresolved hold create journal cannot be cleared')
            if self.live and self.pending.exists():
                self.pending.unlink()
                fd = os.open(self.pending.parent,os.O_RDONLY)
                try:os.fsync(fd)
                finally:os.close(fd)


def reconcile(adapter, *, coordinator=False):
    """Discover cleanup candidates; never infer identity for a SID-less create.

    Calls list uses only To/From: StartTime excludes queued calls, and its upper
    bound can exclude the lost response. DateCreated is checked locally.
    Incomplete pages remain blocked rather than following arbitrary provider URLs.
    """
    journal = adapter.journal
    reports = []
    unresolved = False
    for row in journal.entries:
        if row['state'] not in ('unresolved','ended') or row.get('sid'):
            continue
        # Also revalidate old journals resolved by the superseded StartTime rule.
        row['state'] = 'unresolved'
        if (row['role'] not in ('business','owner') or row['call_id'] != journal.call_id
                or row['task_id'] != journal.task_id or row['to'] in (adapter.api.BORA,adapter.line)
                or not re.fullmatch(r'\+1[0-9]{10}',row['to'])
                or row['from'] not in (adapter.line,adapter.api.ALLOW[1])
                or (row['role']=='business' and row['to'] in adapter.api.ALLOW)
                or (row['role']=='owner' and (row['to'] != adapter.api.ALLOW[0] or row['from'] != adapter.line))
                or not row['attempt_at'] <= row['absolute_deadline'] <= row['attempt_at']+2700):
            raise ProviderError('invalid unresolved hold scope; check private create journal')
        journal.persist()  # no legacy ended state may survive a failed identity read
        floor = row['attempt_at']-2
        found = adapter._post('Calls.json?'+urlencode({'To':row['to'],'From':row['from'],'PageSize':1000}),None)
        calls = found.get('calls')
        if not isinstance(calls,list):
            raise ProviderError('unresolved hold create: invalid Calls result')
        candidates, invalid, older = [], 0, 0
        for call in calls:
            try:
                created = stamp(call['date_created'])  # never substitute StartTime
                if not math.isfinite(created):raise ValueError()
                if (not SID.fullmatch(call.get('sid','')) or call.get('to') != row['to']
                        or call.get('from') != row['from'] or call.get('direction') != 'outbound-api'):
                    raise ValueError()
                if created < floor:
                    older += 1
                    continue
                if not created >= floor:raise ValueError()  # NaN fails closed
                candidates.append(call)
            except (KeyError,ValueError,TypeError,AttributeError,OverflowError):
                invalid += 1
        # Persist every candidate SID before ending anything. No candidate is
        # proof that a SID-less request was accounted for.
        row['matches'] = list(dict.fromkeys(c['sid'] for c in candidates))
        for sid in row['matches']:
            if sid not in adapter.sids:adapter.sids.append(sid)
            if sid not in journal.sids:journal.sids.append(sid)
        journal.persist()
        complete = not found.get('next_page_uri') and not found.get('next_page_url')
        report = {'event':'reconcile_candidates','at':time.time(),'role':row['role'],
            'date_created_floor':floor,'upper_bound':None,'complete':bool(complete),
            'ignored_older':older,'invalid_rows':invalid,'coordinator':coordinator,
            'candidates':[{'sid':'[SID ending '+c['sid'][-6:]+']',
                'to':'[NUMBER ending '+row['to'][-4:]+']','from':'[NUMBER ending '+row['from'][-4:]+']',
                'date_created':stamp(c['date_created']),'status':str(c.get('status','unknown'))[:40]}
                for c in candidates]}
        reports.append(report)
        adapter.api.save(adapter.root/'hold-reconcile.json',reports)
        if coordinator:adapter.api.emit(report)
        failed = False
        for index, call in enumerate(candidates):
            try:
                # Process ALL live candidates even if one termination fails.
                if call.get('status') not in TERMINAL:adapter.end_call(call['sid'])
                check = adapter.fetch_call(call['sid'])
                if not check or check.get('sid') != call['sid'] or check.get('status') not in TERMINAL:
                    raise ProviderError('candidate termination unconfirmed')
                journal.ended(call['sid'])
                report['candidates'][index]['confirmed_status'] = check['status']
            except Exception as exc:
                report['candidates'][index]['error'] = type(exc).__name__
                failed = True
        if not candidates or invalid or not complete or failed:
            report['result'] = 'unresolved_cleanup'
            unresolved = True
        elif coordinator:
            row.update(state='ended',resolution='explicit_coordinator_reconciliation',
                coordinator_reconciled_at=time.time(),
                candidate_created_at={c['sid']:stamp(c['date_created']) for c in candidates})
            row.pop('identity_evidence',None)
            row.pop('identified_created_at',None)
            journal.persist()
            report['result'] = 'confirmed_terminal'
        else:
            report['result'] = 'candidates_terminal_pending_coordinator'
            unresolved = True
        adapter.api.save(adapter.root/'hold-reconcile.json',reports)
        if coordinator:
            adapter.api.emit({'event':'reconcile_result','role':row['role'],'result':report['result'],'candidates':report['candidates']})
    if unresolved:
        raise ProviderError('SID-less create remains unresolved; coordinator reconciliation required')
