"""Recorded hold simulation regression and separately authored held-out cases."""
import json
from pathlib import Path
import time
import unittest
from line.detect import HoldTracker, Utterance as U, classify
from test_agent_greetings import replay as agent_replay

ROOT=Path(__file__).resolve().parents[2]
DATA=json.loads((ROOT/'fixtures/hold-sim/delta-run3.json').read_text())
HELD=json.loads((ROOT/'fixtures/hold-sim/queue-announcements-heldout.json').read_text())


def tracker_replay(model=False):
    calls=[]
    def wrong(prompt):
        turns=json.loads(prompt)['turns'];calls.append(prompt)
        return json.dumps(dict(label='human',confidence='high',contingent=True,evidence=list(range(len(turns)))))
    tracker=HoldTracker(wrong if model else None);rows=[];before_queue=0
    for event in DATA['events']:
        if event['turn_id']==DATA['hold_start_turn']:before_queue=len(calls)
        start=time.perf_counter();r=tracker.feed(U(**event))
        if event['speaker']=='business' and int(event['turn_id'])>=10:
            rows.append(dict(turn=event['turn_id'],text=event['text'],label=r.label,evidence=r.evidence,ms=(time.perf_counter()-start)*1000))
    return tracker,dict(rows=rows,unsafe=sum(r['label']=='human' or 'possible_new_party' in r['evidence'] for r in rows),model_calls=len(calls)-before_queue,pre_queue_model_calls=before_queue)


class DeltaRun3Tests(unittest.TestCase):
    def test_recorded_queue_replay(self):
        for mode in (False,True):
            _,r=tracker_replay(mode)
            self.assertEqual(r['unsafe'],0,r['rows'])
            self.assertEqual(r['model_calls'],0)
            for row in r['rows']:
                if row['turn'] in ('12','13','14'):
                    self.assertIn(row['label'],('automated_assistant','ivr'))
                elif int(row['turn'])>=20:self.assertEqual(row['label'],'hold_message')

    def test_agent_controls_after_entire_replay(self):
        for line in HELD['agents']:
            tracker,_=tracker_replay()
            r=tracker.feed(U(line,900000,turn_id='agent'))
            self.assertTrue(r.label=='human' or 'possible_new_party' in r.evidence,(line,r))
            tracker.feed(U('Am I speaking with a live person?',901000,'hermes',turn_id='q'))
            self.assertEqual(tracker.feed(U('Yes, I am.',902000,turn_id='yes')).label,'human')

    def test_heldout_announcements_and_prompts(self):
        for start in ("I'll transfer your call now.",'The wait time is 23 minutes.','[music]','Please hold.'):
            for key,lines in [('announcements',HELD['announcements']),('prompts',HELD['prompts'])]:
                for line in lines:
                    t=HoldTracker();t.feed(U(start,0))
                    t.feed(U('Am I speaking with a live person?',1000,'hermes'))
                    r=t.feed(U(line,2000))
                    self.assertEqual(r.label,'hold_message' if key=='announcements' else 'automated_assistant',(line,r))
                    self.assertNotIn('possible_new_party',r.evidence)

    def test_hold_memory_outlives_window(self):
        t=HoldTracker();t.feed(U('The current phone wait time is 23 minutes.'))
        for i in range(30):
            r=t.feed(U('Enjoy the benefits of our service '+str(i),1000+i*1000,turn_id=str(i)))
            self.assertEqual(r.label,'hold_message')
        self.assertEqual(t.feed(U('Hello, this is Dana with reservations.',900000)).label,'human')

    def test_wait_time_is_a_stateless_party_boundary(self):
        r=classify([U("I'm a virtual assistant.",0),U('The wait time is twelve minutes.',1000),U('May I have your confirmation number?',2000)])
        self.assertEqual(r.label,'unknown');self.assertIn('possible_new_party',r.evidence)

    def test_recorded_broadcast_cannot_answer_our_question(self):
        for event in DATA['events']:
            if event['speaker']=='business' and int(event['turn_id'])>=20:
                t=HoldTracker();t.feed(U('Please hold.'))
                t.feed(U('Am I speaking with a live person?',1000,'hermes'))
                r=t.feed(U(event['text'],2000))
                self.assertNotEqual(r.label,'human');self.assertNotIn('possible_new_party',r.evidence)

    def test_existing_agent_and_bot_targets(self):
        r=agent_replay();self.assertEqual(r['false_humans'],0);self.assertEqual(r['missed_humans'],0)

if __name__=='__main__':
    print(json.dumps([tracker_replay(mode)[1] for mode in (False,True)],indent=2))
