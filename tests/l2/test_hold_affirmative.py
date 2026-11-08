"""B15: a shared identity affirmative is never recorded queue speech."""
import unittest
from line.detect import HoldTracker, Utterance as U, classify
from line.detect.affirmative import CLEAR_YES


class HoldAffirmativeTests(unittest.TestCase):
    def test_shared_yes_after_question_and_intervening_greeting(self):
        for gap in (False, True):
            for text in sorted(CLEAR_YES | {"Yes, I'm a live person.", 'Yes, this is Dana.'}):
                with self.subTest(intervening=gap,reply=text):
                    t=HoldTracker()
                    turns=[U("I'm a virtual assistant.",0),U('Please hold.',1000),
                           U('Am I speaking with a live person?',2000,'hermes')]
                    if gap:turns.append(U('Hello, how can I help?',2500))
                    for u in turns:t.feed(u)
                    reply=U(text,3000)
                    for r in (t.feed(reply),classify(turns+[reply])):
                        self.assertTrue(r.label=='human' or 'possible_new_party' in r.evidence,r)
                        if gap:self.assertNotEqual(r.label,'human')

    def test_affirmative_without_recent_question_cannot_confirm(self):
        for at in (1000,12001):
            t=HoldTracker();t.feed(U('Please hold.',0))
            if at==12001:t.feed(U('Am I speaking with a live person?',1000,'hermes'))
            self.assertNotEqual(t.feed(U("Yes, I'm a live person.",at)).label,'human')
