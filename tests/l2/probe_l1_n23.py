"""N23 copied answer table through L1's real detector/engine and fake providers.
Uses the installed L1 test environment; no live network/provider/model access.
"""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'l1'))
from test_r27_new_party import ContractBase, N21Tests, BOT, QUESTION

NEGATIVE=['Hello, how can I help?','May I have your confirmation number?','Is that correct?',
          'Would you like us to call you back?','Would you like to make a payment?',
          'No.','Maybe.',"Yes, I'm not a live person."]
POSITIVE=['Yes','Speaking','This is a real person','I am a live agent']


class N23EngineProbe(ContractBase):
    real_detector=True

    def test_reviewer_answers_do_not_ring_without_affirmation(self):
        for answer in NEGATIVE:
            with self.subTest(answer=answer):
                line,req,session,relay,rows=self.run_seq([BOT,'Let me connect you to an agent.',
                                                       'Hello, how can I help?',answer])
                self.assertTrue(any(QUESTION in text for text in rows[2]))
                self.assertFalse(session.human_confirmed)
                self.assertEqual(self.owner_rung(line),[])
                self.assert_never_model_text(relay)

    def test_real_detector_uses_unchanged_budget_controls(self):
        # Round 8 removes the real-detector override that expected question-back HUMAN.
        # Exercise the original 20s/4-question assertions through the real tracker.
        N21Tests.test_N21_question_budget_one_per_20_seconds_four_per_call(self)

    def test_affirmatives_and_named_transfer_controls_ring(self):
        for answer in POSITIVE:
            with self.subTest(answer=answer):
                line,req,session,relay,rows=self.run_seq([BOT,'Let me connect you to an agent.',
                                                       'Hello, how can I help?',answer])
                line.wait_state(req['call_id'],'ringing_owner')
                self.assertTrue(session.human_confirmed)
                self.assertEqual(len(self.owner_rung(line)),1)
        line,req,session,relay,rows=self.run_seq([BOT,'Let me connect you to an agent.',
                                               'Hello, this is Dana. How can I help?'])
        line.wait_state(req['call_id'],'ringing_owner')
        self.assertTrue(session.human_confirmed)
        self.assertFalse(any(QUESTION in t for row in rows for t in row))

if __name__=='__main__':
    unittest.main(verbosity=2)
