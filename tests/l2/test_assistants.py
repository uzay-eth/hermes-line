"""Masked Delta replay and explicitly authored virtual assistant simulations."""
import json
from pathlib import Path
import time
import unittest
from line.detect import Utterance as U, HoldTracker, classify, LABELS

ROOT=Path(__file__).resolve().parents[2]


def replay(with_model):
    corpus=json.loads((ROOT/'fixtures/hold-sim/assistants.json').read_text())
    calls=[]; rows=[]
    def wrong_model(prompt):
        turns=json.loads(prompt)['turns'];calls.append(prompt)
        return json.dumps(dict(label='human',confidence='high',contingent=True,evidence=list(range(len(turns)))))
    matrix={l:{p:0 for p in LABELS} for l in LABELS}
    for stream in corpus['streams']:
        tracker=HoldTracker(wrong_model if with_model else None)
        for i,event in enumerate(stream['events']):
            begin=time.perf_counter()
            result=tracker.feed(U(event['text'],i*3000,turn_id=str(i)),{'state':'on_hold'})
            elapsed=(time.perf_counter()-begin)*1000
            rows.append(dict(stream=stream['id'],expected=event['label'],label=result.label,latency_ms=elapsed))
            matrix[event['label']][result.label]+=1
    return dict(mode='overconfident fake' if with_model else 'no model',events=len(rows),matrix=matrix,
                false_humans=sum(r['expected']!='human' and r['label']=='human' for r in rows),
                missed_humans=sum(r['expected']=='human' and r['label']!='human' for r in rows),
                model_calls=len(calls),max_ms=max(r['latency_ms'] for r in rows),rows=rows)


class AssistantTests(unittest.TestCase):
    def test_replay_classification_and_fast_local_path(self):
        for mode in (False,True):
            r=replay(mode)
            self.assertEqual(r['false_humans'],0)
            self.assertEqual(r['missed_humans'],0)
            self.assertEqual(r['model_calls'],0)
            self.assertTrue(all(x['expected']==x['label'] for x in r['rows']))
            self.assertLess(r['max_ms'],5)

    def test_post_bot_confirmation_requires_clear_affirmative(self):
        for text in ('Yes','Speaking',"It's me",'How can I help you today?'):
            t=HoldTracker();t.feed(U("I'm a virtual assistant.",turn_id='bot'))
            t.feed(U('Am I speaking with a live person?',1000,'hermes',turn_id='q'))
            result=t.feed(U(text,2000,turn_id='answer'))
            if text in ('Yes','Speaking'):self.assertEqual(result.label,'human')
            else:
                self.assertEqual(result.label,'unknown')
                self.assertIn('possible_new_party',result.evidence)

    def test_bot_memory_outlives_window_and_cache(self):
        t=HoldTracker();t.feed(U("I'm an automated assistant.",turn_id='bot'))
        for i in range(300):
            result=t.feed(U('Routing question '+str(i),i*1000,turn_id=str(i)))
            self.assertEqual(result.label,'automated_assistant')
        self.assertLessEqual(len(t.window),12);self.assertLessEqual(len(t._cache),128)

    def test_transfer_alone_and_new_voice_claim_are_not_human(self):
        t=HoldTracker();t.feed(U("I'm a virtual assistant."))
        self.assertEqual(t.feed(U('Let me connect you to an agent.',1000)).label,'hold_message')
        self.assertEqual(t.feed(U('Hi.',2000),{'speaker_changed':True}).label,'unknown')
        self.assertEqual(t.feed(U('This is Morgan, your reservations agent.',3000)).label,'human')

    def test_renewed_bot_disclosure_cancels_pending_handoff(self):
        t=HoldTracker()
        for i,text in enumerate(("I'm a virtual assistant.",'Let me connect you to an agent.',
                                 "I'm another virtual assistant.","Hi, I'm Ava, your virtual assistant, how can I help?")):
            result=t.feed(U(text,i*1000,turn_id=str(i)),{'state':'on_hold'})
            self.assertNotEqual(result.label,'human')

    def test_cached_transfer_rearms_after_renewed_disclosure(self):
        t=HoldTracker()
        for i,text in enumerate(("I'm a virtual assistant.",'Let me connect you to an agent.',
                                 "I'm another virtual assistant.",'Let me connect you to an agent.')):
            t.feed(U(text,i*1000,turn_id=str(i)))
        self.assertEqual(t.feed(U('This is Morgan, your reservations agent.',5000)).label,'human')

    def test_standalone_role_and_automation_denials(self):
        self.assertEqual(classify([U('This is Morgan, your reservations agent.')]).label,'human')
        for text in ("I'm not a live person.","I'm your virtual assistant.",
                     'Hi, this is our assistant with support, how can I help?',
                     'This is our automated support agent.'):
            self.assertNotEqual(classify([U(text)],{'state':'on_hold'}).label,'human')

    def test_keypad_and_speech_route_are_distinct(self):
        self.assertEqual(classify([U('Press one to update a reservation.')]).label,'ivr')
        self.assertEqual(classify([U('Are you updating a reservation?')]).label,'automated_assistant')

    def test_hold_music_requires_explicit_text_marker(self):
        self.assertEqual(classify([U('[music]')]).label,'hold_music')
        for text in ('','um uh','inaudible'):
            self.assertEqual(classify([U(text)]).label,'unknown')
        self.assertEqual(classify([U('Your call is important, please hold.')]).label,'hold_message')

    def test_partial_bot_disclosure_does_not_latch(self):
        t=HoldTracker();t.feed(U("I'm an automated assistant.",last=False))
        self.assertEqual(t.feed(U('Hello?',1000)).label,'unknown')

if __name__=='__main__':
    print(json.dumps([replay(False),replay(True)],indent=2))
