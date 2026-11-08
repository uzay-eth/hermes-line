"""R2 B1 reviewer cases; B19 near affirmatives stay uncertain person candidates."""
import json
from pathlib import Path
import unittest
from line.detect import HoldTracker, Utterance as U
DATA=json.loads((Path(__file__).resolve().parents[2]/'fixtures/hold-sim/r2-agent-openers.json').read_text())


def setup(pre):
    t=HoldTracker()
    for i,(text,speaker) in enumerate(pre):t.feed(U(text,i*5000,speaker))
    return t


def scores():
    rows=[]
    for pre in ('DELTA_PRE','PLAIN_PRE'):
        for text in DATA['AGENTS']+[DATA['disconnect']]:
            r=setup(DATA[pre]).feed(U(text,90000))
            rows.append(dict(start=pre,text=text,label=r.label,evidence=r.evidence))
    return dict(rows=rows,missed_candidates=sum(r['label']!='human' and 'possible_new_party' not in r['evidence'] for r in rows))


class R2OpenersTests(unittest.TestCase):
    def test_all_openers_and_disconnect(self):
        r=scores();self.assertEqual(r['missed_candidates'],0,r['rows'])

    def test_disconnect_followup(self):
        t=setup(DATA['DELTA_PRE'])
        t.feed(U(DATA['AGENTS'][0],90000))
        r=t.feed(U(DATA['disconnect'],92000))
        self.assertIn('possible_new_party',r.evidence)

    def test_reviewer_split_and_prefixed_sequences(self):
        for seq in DATA['SEQUENCES']:
            t=setup(DATA['DELTA_PRE'])
            for i,text in enumerate(seq):
                r=t.feed(U(text,90000+i*1000))
                self.assertTrue(r.label=='human' or 'possible_new_party' in r.evidence,(text,r))

    def test_automation_in_same_line_wins(self):
        for text in DATA['AGENTS']:
            r=setup(DATA['DELTA_PRE']).feed(U(text+" I'm a virtual assistant.",90000))
            self.assertEqual(r.label,'automated_assistant')

    def test_near_affirmative_is_not_high_confidence(self):
        # Round 43c (MIT-018 STEER-4, the three Hilton calls): an explicit affirmation at the start of a reply to our
        # question is a yes whatever follows, unless the reply says what bots, menus or recordings say.
        for text in ("Yeah, I'm here.",'Yes, how can I help you?','Uh, yes.','I am.','Yes. Are you a live person too?',
                     'Yes. Are you a robot?',"I'm a live person.",
                     'Yes. You are speaking with the live person. Yes. Indeed.',
                     'You are speaking to a live person. How can I help you?'):
            t=setup(DATA['DELTA_PRE'])
            t.feed(U('Am I speaking with a live person?',90000,'hermes'))
            r=t.feed(U(text,92000))
            self.assertEqual(r.label,'human',text)
        for text in ('Yes? Are you there?',"Yes, I'm a virtual assistant.",'Yes, no.','Yes, please press one.',
                     'Yes, would you like to make a payment?','Hello? Are you a live person too?'):
            t=setup(DATA['DELTA_PRE'])
            t.feed(U('Am I speaking with a live person?',90000,'hermes'))
            self.assertNotEqual(t.feed(U(text,92000)).label,'human',text)

if __name__=='__main__':print(json.dumps(scores(),indent=2))
