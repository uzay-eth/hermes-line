"""N23 sequences copied from r1d25/fixtures/new_party.py (read only).
Authored controls extend the coordinator's round 8 policy; no external I/O.
"""
import json
import unittest
from line.detect import HoldTracker, Utterance as U, classify

BOT="I'm Delta's automated assistant."
Q='Am I speaking with a live person?'
NEGATIVE=['Hello, how can I help?','May I have your confirmation number?', 'Is that correct?',
          'Would you like us to call you back?', 'Would you like to make a payment?',
          'No.', 'Maybe.', "Yes, I'm not a live person.", 'Yes, no.', 'Yes, I am virtual.',
          'Yes, I am automated.', 'Speaking? Is that correct?', 'Yes, would you like to make a payment?']
AFFIRMATIVE=['Yes','Yes I am','Yeah.','Speaking','This is a real person','I am a live agent',
             "Yes, I'm a live person.","I'm a real person.",'Yes, you are speaking to a live agent.']


def answer(text, with_bot=True, bot_only_label=False, model=None):
    t=HoldTracker(model)
    if with_bot:t.feed(U('Are you calling about an existing booking?' if bot_only_label else BOT,0,turn_id='bot'))
    t.feed(U('Let me connect you to an agent.',1000,turn_id='transfer'))
    t.feed(U('Hello, how can I help?',2000,turn_id='greeting'))
    t.feed(U(Q,2500,'hermes',turn_id='q'))
    return t.feed(U(text,3000,turn_id='reply'))


def scores():
    rows=[]
    for heuristic in (False,True):
        for text in NEGATIVE+AFFIRMATIVE:
            r=answer(text,bot_only_label=heuristic)
            rows.append(dict(heuristic_bot=heuristic,text=text,affirmative=text in AFFIRMATIVE,label=r.label,confidence=r.confidence,evidence=r.evidence))
    return dict(rows=rows,false_humans=sum(not r['affirmative'] and r['label']=='human' for r in rows),
                missed_affirmatives=sum(r['affirmative'] and r['label']!='human' for r in rows),
                negative_cases=2*len(NEGATIVE),affirmative_cases=2*len(AFFIRMATIVE))


class ClearConfirmationTests(unittest.TestCase):
    def test_n23_table_and_clear_affirmatives(self):
        r=scores();self.assertEqual(r['false_humans'],0);self.assertEqual(r['missed_affirmatives'],0)
        for row in r['rows']:
            if not row['affirmative']:self.assertIn(row['label'],('unknown','automated_assistant'))

    def test_silence_only_named_bot_is_never_human(self):
        for gap in (7999,8000,10000,60000):
            t=HoldTracker();t.feed(U(BOT,0))
            r=t.feed(U('My name is Alex. How can I help you?',gap))
            self.assertEqual((r.label,r.confidence),('unknown','low'))
            self.assertIn('possible_new_party',r.evidence)

    def test_transfer_and_music_named_controls_still_human(self):
        for boundary in ('Let me connect you to an agent.','[music]','Please hold.'):
            t=HoldTracker();t.feed(U(BOT,0));t.feed(U(boundary,1000))
            self.assertEqual(t.feed(U('My name is Alex. How can I help?',10000)).label,'human')

    def test_pre_bot_question_back_stays_legacy(self):
        self.assertEqual(answer('Hello, how can I help?',with_bot=False).label,'human')

    def test_ever_bot_label_survives_resolved_human_and_window(self):
        t=HoldTracker();t.feed(U(BOT,0));t.feed(U('Please hold.',1000))
        self.assertEqual(t.feed(U('This is Dana, your reservations agent.',2000)).label,'human')
        for i in range(20):t.feed(U('um',3000+i*100))
        t.feed(U('Please hold.',6000));t.feed(U(Q,6500,'hermes'))
        self.assertNotEqual(t.feed(U('Would you like to make a payment?',7000)).label,'human')

    def test_model_cannot_override_nonaffirmative(self):
        calls=[]
        def wrong(p):calls.append(p);return '{"label":"human","confidence":"high","evidence":[0,1],"contingent":true}'
        self.assertNotEqual(answer('Is that correct?',model=wrong).label,'human')
        self.assertFalse(calls)

    def test_stateless_window_with_bot_uses_strict_confirmation(self):
        for text in NEGATIVE:
            r=classify([U(BOT,0),U('Please hold.',1000),U(Q,2000,'hermes'),U(text,3000)])
            self.assertNotEqual(r.label,'human')

if __name__=='__main__':print(json.dumps(scores(),indent=2))
