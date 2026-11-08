"""Offline L1 stale-command probe with the round 6 immediate named-human transition.

Uses the existing L1 loopback/fake-provider harness and its existing environment.
The original L1 test remains untouched for coordinator review.
"""
from pathlib import Path
import sys
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'l1'))
from harness import Line, wait_for


def main():
    line=Line()
    try:
        req,created=line.dial()
        relay=line.connect(req,created)
        relay.prompt('Please hold, your call is important to us.')
        line.wait_state(req['call_id'],'on_hold')
        gate=threading.Event()
        real_claim=line.store.claim_commands
        line.store.claim_commands=lambda limit=20: real_claim(limit) if gate.is_set() else []
        revision=line.store.get_call(req['call_id']).control_revision
        out=line.svc.submit('say',source='desktop',call_id=req['call_id'],payload={'text':'Hi Mike!'},expected_revision=revision,wait_s=.2)
        assert out['status']=='queued'
        relay.prompt('Thanks for waiting, this is Dana.')
        line.wait_state(req['call_id'],'agent_talking')
        gate.set();line.svc.wake()
        done=wait_for(lambda:(line.store.get_command(out['command_id']) or {}).get('result'))
        assert done['error']=='stale_revision' and done['stale']
        time.sleep(.2)
        assert 'Hi Mike!' not in relay.spoken()
        assert 'command_stale' in [e['kind'] for e in line.store.events(req['call_id'])]
        print('PASS: immediate named-human transition; queued stale say command rejected; no stale speech.')
    finally:
        line.close()

if __name__=='__main__':main()
