import json
import unittest
from line.detect import Utterance as U, HoldTracker, classify

Q=U('Are you a live person responding to my request?',1000,'hermes',turn_id='q')

def model(label='human',confidence='high',evidence=None,contingent=True):
    return lambda prompt: json.dumps(dict(label=label,confidence=confidence,evidence=[0,1] if evidence is None else evidence,contingent=contingent))

class DetectTests(unittest.TestCase):
    def test_recorded_hello_rules_precede_model(self):
        def fail(_): self.fail('model called for deterministic rule')
        self.assertEqual(classify([U('Hello, thanks for holding.')],{},fail).label,'hold_message')

    def test_hello_alone_never_human(self):
        for text in ('Hello?','Hi'):
            self.assertEqual(classify([Q,U(text,2000)],{},model()).label,'unknown')

    def test_confirmation_explicit(self):
        self.assertEqual(classify([Q,U('Yes, I am a live agent.',2000)]).label,'human')

    def test_no_confirmation_no_human(self):
        self.assertNotEqual(classify([U('How can I help you?')],{},model(evidence=[0])).label,'human')

    def test_contextual_model_without_name_is_capped(self):
        r=classify([Q,U('I can help with your appointment.',2000)],{},model())
        self.assertEqual(r.label,'unknown')
        self.assertIn('possible_new_party',r.evidence)

    def test_stale_confirmation(self):
        self.assertEqual(classify([Q,U('I can help.',22000)],{},model()).label,'unknown')

    def test_uncertain_model_never_human(self):
        for confidence in ('low','medium',.99):
            self.assertEqual(classify([Q,U('I can help.',2000)],{},model(confidence=confidence)).label,'unknown')

    def test_invalid_model(self):
        for raw in ('human','{}','[]','null','x'*4097,'{"label":"fax"}'):
            self.assertEqual(classify([U('Novel speech')],{},lambda _:raw).label,'unknown')

    def test_model_exception(self):
        def fail(_): raise RuntimeError('unavailable')
        self.assertEqual(classify([U('Novel speech')],{},fail).label,'unknown')

    def test_bad_evidence_and_noncontingent(self):
        for fn in (model(evidence=[0]),model(evidence=[1]),model(evidence=[True,1]),model(evidence=[-1,1]),model(contingent=False)):
            self.assertEqual(classify([Q,U('I can help.',2000)],{},fn).label,'unknown')

    def test_rules_voicemail_ivr_hold(self):
        for text,label in [('Leave your name and number.','voicemail'),('[beep]','voicemail'),('Press 3 for billing.','ivr'),('Please hold.','hold_message')]:
            self.assertEqual(classify([U(text)]).label,label)

    def test_repeated_novel_recording(self):
        self.assertEqual(classify([U('Welcome to the service center.',0,turn_id='1'),U('Welcome to the service center!',5000,turn_id='2')]).label,'hold_message')

    def test_silence_partial_and_owner(self):
        for u in (U(''),U('Yes, I am a live agent.',last=False),U('Hello',speaker='owner')):
            self.assertEqual(classify([u]).label,'unknown')

    def test_partial_final_replacement(self):
        t=HoldTracker(); t.feed(Q)
        self.assertEqual(t.feed(U('Yes, I am',1500,last=False,turn_id='a')).label,'unknown')
        self.assertEqual(t.feed(U('Yes, I am a live agent.',2000,turn_id='a')).label,'human')
        self.assertEqual(t.feed(U('Yes, I am a live agent.',2100,turn_id='a')).label,'unknown')

    def test_model_called_only_once_for_repeated_unknown(self):
        calls=[]
        def fake(p): calls.append(p); return '{"label":"unknown","confidence":"low","evidence":[0]}'
        t=HoldTracker(fake)
        for i in range(10): t.feed(U('Some novel statement',i,turn_id=str(i)))
        self.assertEqual(len(calls),1)
        result=t.feed(U('Some novel statement',100,turn_id='new'))
        self.assertEqual(result.label,'unknown')
        self.assertIn('possible_new_party',result.evidence)

    def test_malformed_utterance_fails_closed(self):
        for u in (None, U('Hello', at_ms=-1), U('Hello', at_ms='0'),
                  U('Hello', last='false'), U('Hello', turn_id=[]),
                  U('Hello', speaker='untrusted')):
            self.assertEqual(HoldTracker().feed(u).label,'unknown')
            self.assertEqual(classify([u]).label,'unknown')

    def test_no_human_replay_on_new_id(self):
        t=HoldTracker(); t.feed(Q)
        self.assertEqual(t.feed(U('Yes, I am a live agent.',2000,turn_id='a')).label,'human')
        self.assertEqual(t.feed(U('Yes, I am a live agent.',3000,turn_id='b')).label,'unknown')

    def test_bounded_memory(self):
        t=HoldTracker()
        for i in range(3000): t.feed(U(f'Novel {i}',i,turn_id=str(i)))
        self.assertLessEqual(len(t.window),12)
        self.assertLessEqual(len(t._cache),128)
        self.assertLessEqual(len(t._seen),256)

    def test_invalid_or_oversized_text(self):
        t=HoldTracker()
        for value in (None,'x'*2001):
            self.assertEqual(t.feed(U(value)).label,'unknown')

    def test_no_amd_or_duration_only_confirmation(self):
        self.assertEqual(classify([U('Hello?',2100000)],{'amd':'human','elapsed_s':2100},model(evidence=[0])).label,'unknown')


class ConfirmationRound2Tests(unittest.TestCase):
    def test_natural_answers_without_model(self):
        for answer in ("Yes", "Yeah", "Yep.", "Speaking", "It's me", "It's me, [NAME].",
                       "Morgan", "Morgan Lee", "I'm Morgan.", "My name is Morgan.",
                       "[NAME]", "What information do you have about me?", "Who is calling?",
                       "Hi. How are you?", "Yeah. It's me, [NAME]. But information you have about me?"):
            for delta in (0, 9739, 10000):
                with self.subTest(answer=answer, delta=delta):
                    result=classify([Q,U(answer,Q.at_ms+delta)])
                    self.assertEqual((result.label,result.confidence),('human','high'))
                    self.assertEqual(result.evidence,(Q.text,answer))

    def test_outside_ten_seconds_and_before_question(self):
        for delta in (-1,10001,20000):
            for answer in ('Yes','Speaking',"It's me",'Morgan','Who is calling?'):
                result=classify([Q,U(answer,Q.at_ms+delta)],{},model())
                self.assertEqual(result.label,'unknown')

    def test_answers_without_question_do_not_confirm(self):
        for answer in ('Yes','Yeah','Speaking',"It's me",'Morgan','Who is calling?','Hi. How are you?'):
            self.assertEqual(classify([U(answer)],{},model(evidence=[0])).label,'unknown')

    def test_disclosure_and_unrelated_human_questions_do_not_confirm(self):
        for question in ('This is an AI assistant calling for [OWNER].',
                         'Would you like human resources?', 'Is a human needed?',
                         'What did the live person say?'):
            self.assertEqual(classify([U(question,1000,'hermes'),U('Yes',2000)],{},model()).label,'unknown')

    def test_machine_messages_override_confirmation(self):
        for answer,label in [('Yes, please hold.','hold_message'),
                             ('Hello, thanks for holding.','hold_message'),
                             ('Yes, press one for sales.','ivr'),
                             ('This is Morgan. Leave a message after the beep.','voicemail')]:
            self.assertEqual(classify([Q,U(answer,2000)],{},model()).label,label)

    def test_negative_and_uncertain_answers(self):
        for answer in ('No.', 'No, this is not a live person.', "I am not a live person.",
                       'Maybe.', "I don't know.", 'One moment.', 'Good Afternoon.'):
            self.assertIn(classify([Q,U(answer,2000)]).label,('unknown','automated_assistant'))

    def test_only_first_final_reply_uses_question(self):
        self.assertEqual(classify([Q,U('Hello?',2000),U('Yes',3000)]).label,'unknown')

    def test_intervening_hermes_turn_invalidates_question(self):
        self.assertEqual(classify([Q,U('Please wait.',1500,'hermes'),U('Yes',2000)]).label,'unknown')

    def test_cached_unknown_does_not_hide_answer_to_new_question(self):
        t=HoldTracker()
        self.assertEqual(t.feed(U('Yeah',0,turn_id='early')).label,'unknown')
        t.feed(Q)
        self.assertEqual(t.feed(U('Yeah',2000,turn_id='answer')).label,'human')
        self.assertEqual(t.feed(U('Yeah',2500,turn_id='answer')).label,'unknown')
        t.feed(U(Q.text,3000,'hermes',turn_id='q2'))
        self.assertEqual(t.feed(U('Yeah',4000,turn_id='answer2')).label,'human')

    def test_cached_answer_does_not_bypass_expiry(self):
        t=HoldTracker(); t.feed(Q)
        self.assertEqual(t.feed(U('Yes',2000,turn_id='a')).label,'human')
        self.assertEqual(t.feed(U('Yes',20000,turn_id='b')).label,'unknown')
