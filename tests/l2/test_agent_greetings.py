"""Authored greeting regressions; no provider/model I/O."""
import json
from pathlib import Path
import time
import unittest
from line.detect import HoldTracker, Utterance as U, classify

ROOT=Path(__file__).resolve().parents[2]
DATA=json.loads((ROOT/'fixtures/hold-sim/agent-greetings.json').read_text())


def replay():
    contexts=[('hold_message',['Your call is important, please hold.']),('hold_music',['[music]'])]
    contexts += [('transfer-'+str(i),["I'm a virtual assistant.",line]) for i,line in enumerate(DATA['transfers'])]
    rows=[]
    for kind,prefix in contexts:
        for expected,texts in [('human',DATA['humans']),('not_human',DATA['bots'])]:
            for text in texts:
                t=HoldTracker()
                for i,p in enumerate(prefix):t.feed(U(p,i*1000,turn_id=str(i)))
                start=time.perf_counter()
                r=t.feed(U(text,2000,turn_id='answer'))
                rows.append(dict(context=kind,text=text,expected=expected,label=r.label,confidence=r.confidence,
                                 ms=(time.perf_counter()-start)*1000))
    return dict(humans=sum(r['expected']=='human' for r in rows),
                bots=sum(r['expected']=='not_human' for r in rows),
                missed_humans=sum(r['expected']=='human' and (r['label']!='human' or r['confidence']!='high') for r in rows),
                false_humans=sum(r['expected']=='not_human' and r['label']=='human' for r in rows),
                max_ms=max(r['ms'] for r in rows),rows=rows)


class AgentGreetingTests(unittest.TestCase):
    def test_no_false_bot_humans_or_missed_agents(self):
        r=replay()
        self.assertEqual(r['missed_humans'],0)
        self.assertEqual(r['false_humans'],0)

    def test_reported_greetings_standalone(self):
        for text in DATA['humans'][:3]:
            r=classify([U(text)])
            self.assertEqual((r.label,r.confidence),('human','high'))

    def test_offers_of_help_are_neutral_without_identity(self):
        for text in ('How can I help you today?','How may I assist you?','Can I help you?','May I help?'):
            self.assertEqual(classify([U(text)]).label,'unknown')

    def test_transfer_alone_does_not_establish_personhood(self):
        for text in DATA['transfers']:
            t=HoldTracker();t.feed(U("I'm a virtual assistant."))
            self.assertNotIn(t.feed(U(text,1000)).label,('human','automated_assistant'))
            self.assertEqual(t.feed(U('Hi.',2000)).label,'unknown')

    def test_named_greeting_without_boundary_is_possible_party(self):
        t=HoldTracker();t.feed(U("I'm a virtual assistant."))
        result=t.feed(U('Hi, this is Dana, how can I help?',1000))
        self.assertEqual(result.label,'unknown')
        self.assertIn('possible_new_party',result.evidence)
        self.assertEqual(t.feed(U('How may I assist you?',2000)).label,'automated_assistant')

    def test_callback_offer_with_stay_on_line_is_not_plain_hold(self):
        text='To get a call back when an agent is available, say yes. To stay on the line, say no.'
        self.assertEqual(classify([U(text)]).label,'automated_assistant')

    def test_recorded_or_quoted_names_do_not_confirm(self):
        for text in ('Your agent may say this is Dana. Please hold.',
                     'This recorded message says: my name is Dana.',
                     'Your call is important. Dana speaking on this recording.'):
            t=HoldTracker();t.feed(U('Please hold.'))
            self.assertNotEqual(t.feed(U(text,1000)).label,'human')

if __name__=='__main__':print(json.dumps(replay(),indent=2))
