import json
import unittest
from evaluate import ROOT, evaluate

class SimulationTests(unittest.TestCase):
    def test_manifest_splits_and_no_script_leakage(self):
        paths=[ROOT/'fixtures'/'hold-sim'/f'{name}.json' for name in ('train','held-out')]
        corpora=[json.loads(p.read_text()) for p in paths]
        self.assertEqual([len(c['streams']) for c in corpora],[30,32])
        for c in corpora:
            self.assertTrue(all(s['simulation'] for s in c['streams']))
            self.assertEqual(len({s['id'] for s in c['streams']}),len(c['streams']))
        scripts=[{e['text'] for s in c['streams'] for e in s['events'] if e['speaker']=='business' and e['text'] not in ('','Hello?')} for c in corpora]
        self.assertFalse(scripts[0]&scripts[1])

    def test_zero_false_human_release_gate(self):
        for split in ('train','held-out','soak'):
            for mode in (False,True):
                with self.subTest(split=split,mode=mode):
                    self.assertEqual(evaluate(split,mode)['false_human'],0)

    def test_virtual_soak_duration(self):
        corpus=json.loads((ROOT/'fixtures'/'hold-sim'/'soak.json').read_text())
        self.assertEqual(len(corpus['streams']),6)
        self.assertTrue(all(s['duration_ms']==35*60*1000 for s in corpus['streams']))
        self.assertEqual(sum(s['duration_ms'] for s in corpus['streams']),12600000)

    def test_unconfirmed_short_human_remains_a_reported_miss(self):
        # Safety takes priority; do not relabel real short human as UNKNOWN truth.
        for mode in (False,True):
            result=evaluate('held-out',mode)
            self.assertGreaterEqual(result['missed_human_episodes'],1)

    def test_round2_live_cases(self):
        for mode in (False,True):
            predictions=evaluate('held-out',mode)['predictions']
            self.assertEqual([p[3] for p in predictions if p[0]=='held-out-live-disclosure-greeting'],['unknown'])
            self.assertEqual([p[3] for p in predictions if p[0]=='held-out-live-confirmation-answer'],['human'])

    def test_original_31_nonhuman_events_stay_nonhuman(self):
        before=json.loads((ROOT/'tests'/'l2'/'round2-before.json').read_text())
        for row in before:
            if row['split']!='held-out': continue
            expected=[p for p in row['predictions'] if p[2]!='human']
            actual=[p for p in evaluate('held-out',row['mode']=='fake model')['predictions'] if p[2]!='human']
            self.assertEqual(len(expected),31)
            self.assertEqual([p[:3] for p in actual],[p[:3] for p in expected])
            self.assertTrue(all(p[3]!='human' for p in actual))
