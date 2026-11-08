"""Masked run-2 replay and authored brand-routing variants; no external I/O."""
import json
from pathlib import Path
import time
import unittest
from line.detect import HoldTracker, Utterance as U, classify
from line.detect.affirmative import clear_yes, CLEAR_YES
from test_agent_greetings import replay as agent_replay

ROOT=Path(__file__).resolve().parents[2]
DATA=json.loads((ROOT/'fixtures/hold-sim/delta-run2.json').read_text())
Q=U('Am I speaking with a live person?',1000,'hermes',turn_id='q')


def replay():
    calls=[];rows=[]
    def wrong_model(prompt):
        turns=json.loads(prompt)['turns'];calls.append(prompt)
        return json.dumps(dict(label='human',confidence='high',contingent=True,evidence=list(range(len(turns)))))
    tracker=HoldTracker(wrong_model)
    for e in DATA['events']:
        start=time.perf_counter();r=tracker.feed(U(**e),{'state':'ivr','task':'Delta checked-bag fee'})
        if e['speaker']=='business':rows.append(dict(text=e['text'],label=r.label,confidence=r.confidence,evidence=r.evidence,ms=(time.perf_counter()-start)*1000))
    return dict(rows=rows,false_humans=sum(r['label']=='human' for r in rows),model_calls=len(calls),max_ms=max(r['ms'] for r in rows))


class DeltaRun2Tests(unittest.TestCase):
    def test_full_call_replay_never_human(self):
        result=replay();self.assertEqual(result['false_humans'],0)
        self.assertEqual(result['model_calls'],0)

    def test_brand_questions_fast_assistant_without_context(self):
        def fail(_):self.fail('deterministic brand question called model')
        for text in DATA['brand_questions']:
            r=classify([U(text)],ask_model=fail)
            self.assertEqual(r.label,'automated_assistant')

    def test_truncated_intro_never_human_even_after_hold_or_question(self):
        for text in DATA['truncated']:
            for prefix in ([],[U('Please hold.',0)],[Q]):
                r=classify(prefix+[U(text,2000)],{'state':'on_hold'})
                self.assertEqual(r.label,'automated_assistant')

    def test_no_name_model_human_is_capped(self):
        def wrong(_):return '{"label":"human","confidence":"high","contingent":true,"evidence":[0,1]}'
        for text in ('I can help with your appointment.','The connection seems clear.', 'The requested service is available.'):
            r=classify([Q,U(text,2000)],ask_model=wrong)
            self.assertEqual((r.label,r.confidence),('unknown','low'))
            self.assertIn('possible_new_party',r.evidence)

    def test_personal_name_or_clear_yes_agent_controls(self):
        r=agent_replay();self.assertEqual(r['missed_humans'],0);self.assertEqual(r['false_humans'],0)
        for text in ('My name is Dana. How can I help?', 'Hi, this is Morgan with reservations, how can I help?'):
            self.assertEqual(classify([Q,U(text,2000)]).label,'human')

    def test_every_shared_affirmative_used_by_detector(self):
        # Exercise helper variants directly after a known bot and a question.
        for text in sorted(CLEAR_YES | {'Yes, this is Dana.'}):
            t=HoldTracker();t.feed(U("I'm a virtual assistant.",0))
            t.feed(Q)
            self.assertEqual(t.feed(U(text,2000)).label,'human',text)
        for text in ('Yes, no.','Yes, would you like to make a payment?','Speaking? Is that correct?'):
            self.assertFalse(clear_yes(text))

    def test_slow_model_name_gate_without_question(self):
        def wrong(_):return '{"label":"human","confidence":"high","contingent":true,"evidence":[0]}'
        r=classify([U('A novel statement.')],ask_model=wrong)
        self.assertEqual(r.label,'unknown');self.assertIn('possible_new_party',r.evidence)

if __name__=='__main__':print(json.dumps(replay(),indent=2))
