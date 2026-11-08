"""N1: explicit menus, recordings and voicemail precede person cues in hold."""
import json
from pathlib import Path
import unittest
from line.detect import HoldTracker, Utterance as U
DATA=json.loads((Path(__file__).resolve().parents[2]/'fixtures/hold-sim/r2-explicit-negatives.json').read_text())


class ExplicitRuleTests(unittest.TestCase):
    def test_reviewer_negative_labels_win(self):
        for row in DATA['rows']:
            for prefix in (['Please hold.'],["I'm a virtual assistant.","I'll transfer your call now."]):
                for question in (False,True):
                    with self.subTest(line=row['text'],prefix=prefix,question=question):
                        t=HoldTracker()
                        for i,line in enumerate(prefix):t.feed(U(line,i*1000))
                        if question:t.feed(U('Am I speaking with a live person?',3000,'hermes'))
                        r=t.feed(U(row['text'],5000))
                        self.assertEqual(r.label,row['label'])
                        self.assertNotIn('possible_new_party',r.evidence)
