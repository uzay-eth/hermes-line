"""Recorded hold simulation; authored text, virtual time, no provider/model I/O."""
import json
from pathlib import Path
import time
import unittest
from line.detect import HoldTracker, Utterance, classify, LABELS

ROOT = Path(__file__).resolve().parents[2]
CONTEXT = {'state': 'on_hold', 'mode': 'owner'}
INTRO = 'Hi, this is Morgan with Delta, how can I help?'


def score(split, model_enabled):
    corpus = json.loads((ROOT / 'fixtures/hold-sim' / f'airline-{split}.json').read_text())
    calls = []
    def overconfident(prompt):
        turns = json.loads(prompt)['turns']; calls.append(prompt)
        return json.dumps(dict(label='human', confidence='high', contingent=True,
                               evidence=list(range(len(turns)))))
    tracker = HoldTracker(overconfident if model_enabled else None)
    matrix = {label: {p: 0 for p in LABELS} for label in LABELS}
    latencies = []
    for e in corpus['events']:
        start = time.perf_counter()
        result = tracker.feed(Utterance(**{k:e[k] for k in ('text','at_ms','turn_id','speaker','last')}),e['context'])
        elapsed = (time.perf_counter() - start) * 1000
        matrix[e['label']][result.label] += 1
        if e['label']=='human': latencies.append(elapsed)
    return dict(split=split, mode='overconfident fake' if model_enabled else 'no model',matrix=matrix,
                false_human=sum(row['human'] for label,row in matrix.items() if label!='human'),
                missed_human=sum(v for label,v in matrix['human'].items() if label!='human'),
                human_latency_ms=latencies, model_calls=len(calls))


class AirlineHoldTests(unittest.TestCase):
    def test_airline_corpora_no_false_or_missed_humans(self):
        for split in ('train','held-out'):
            for mode in (False,True):
                with self.subTest(split=split,model=mode):
                    r=score(split,mode)
                    self.assertEqual(r['false_human'],0)
                    self.assertEqual(r['missed_human'],0)
                    self.assertEqual(r['model_calls'],0)
                    self.assertLess(max(r['human_latency_ms']),2000)

    def test_noise_cannot_answer_live_person_question(self):
        q=Utterance('Am I speaking with a live person?',0,'hermes')
        for text in ('','[music]','♪ ♫','oh um hmm','la la la','Thank you thank you thank you'):
            self.assertIn(classify([q,Utterance(text,1000)],CONTEXT).label,('unknown','hold_music'))

    def test_45_minute_virtual_hold_and_late_human(self):
        t=HoldTracker()
        negatives=('', '[instrumental music]', 'um ah', 'Your call is important to us.',
                   'Thank you for your patience. We will be with you shortly.')
        for i,at_ms in enumerate(range(0,2640000,15000)):
            self.assertNotEqual(t.feed(Utterance(negatives[i%5],at_ms,turn_id=str(i)),CONTEXT).label,'human')
        # No-prompt intervals cause no detector invocation/state change.
        t.feed(Utterance(INTRO[:20],2640000,last=False,turn_id='agent'),CONTEXT)
        start=time.perf_counter()
        self.assertEqual(t.feed(Utterance(INTRO,2640500,turn_id='agent'),CONTEXT).label,'human')
        self.assertLess(.5 + time.perf_counter()-start,2)
        self.assertEqual(t.feed(Utterance(INTRO,2640600,turn_id='agent'),CONTEXT).label,'unknown')
        self.assertEqual(t.feed(Utterance('',2700000,turn_id='deadline'),CONTEXT).label,'unknown')
        self.assertLessEqual(len(t.window),12)
        self.assertLessEqual(len(t._cache),128)
        self.assertLessEqual(len(t._seen),256)

    def test_strong_named_introduction_is_human_with_or_without_hold(self):
        self.assertEqual(classify([Utterance(INTRO)]).label,'human')
        self.assertEqual(classify([Utterance(INTRO)],CONTEXT).label,'human')

    def test_known_recording_does_not_become_novel_agent(self):
        t=HoldTracker()
        t.feed(Utterance(INTRO,0,turn_id='recording'),{'state':'ivr'})
        self.assertNotEqual(t.feed(Utterance(INTRO,5000,turn_id='again'),CONTEXT).label,'human')

    def test_recorded_greetings_and_menu_rules_win(self):
        for text in ('Hello, thanks for holding. Your call is important.',
                     INTRO+' Please hold.',
                     'This recorded announcement says: '+INTRO,
                     'Hi, this is our automated assistant with Delta, how can I help?'):
            self.assertNotEqual(classify([Utterance(text)],CONTEXT).label,'human')

    def test_callback_offer_is_menu_not_human_in_either_task(self):
        for permitted in (False,True):
            result=classify([Utterance('Press 1 to get a call back.')],dict(CONTEXT,callback_authorized=permitted))
            self.assertEqual(result.label,'ivr')
            self.assertIn('task authorization',result.evidence[0])
            # Detection never returns or executes a digit; actual task consent is L1's job.

    def test_account_identifier_prompts_are_ivr(self):
        for text in ('What is your confirmation number?', 'Enter or say your SkyMiles number.',
                     'Please provide your booking reference.', 'Tell me your frequent flyer number.'):
            self.assertEqual(classify([Utterance(text)],CONTEXT).label,'automated_assistant')

    def test_partial_intro_does_not_ring(self):
        self.assertEqual(classify([Utterance(INTRO,last=False)],CONTEXT).label,'unknown')

if __name__ == '__main__':
    print(json.dumps([score(s,m) for s in ('train','held-out') for m in (False,True)],indent=2))
