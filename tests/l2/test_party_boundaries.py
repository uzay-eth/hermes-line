"""N20/N21 cases copied from read-only r1d24/fixtures/review.py; authored extensions.
No engine, network, model service or scratch-file writes.
"""
import json
import unittest
from line.detect import HoldTracker, Utterance as U, classify

BOT="I'm Delta's automated assistant."
# Copied reviewer sequence text. Timestamps are chosen below per scenario:
# the original script's 10-second intervals themselves count as new boundaries.
REVIEWER_CASES={
 'bot split name':[BOT,'My name is Alex. How can I help you?'],
 'bot explicit name':[BOT,'My name is Alex, your virtual assistant. How can I help?'],
 'human named transfer':[BOT,'Let me connect you to an agent.','Hello, this is Dana. How can I help?'],
 'human unnamed no cue':[BOT,'Hello, how can I help you?','Can I have your confirmation number?','Hello? Are you there?'],
 'human unnamed after music':[BOT,'[music]','Hello, how can I help you?','May I have your confirmation number?'],
 'human unnamed transfer':[BOT,'Let me connect you to an agent.','Hello, how can I help?'],
 'transfer variant':[BOT,'Please wait while I connect you.','Hello, how can I help?'],
}
TRANSFERS=['Let me connect you to an agent.','Transferring you to a representative.',
 'Please hold while I transfer you to an agent.','Please wait while I connect you.',
 'Connecting you now.','One moment while I transfer you.',"I'll get you to someone.",
 'Putting you through.','Let me get an agent.']
AGENTS=['My name is Alex. How can I help you?','Hello, this is Dana. How can I help?',
 'Dana speaking.','Hello, how can I help?','May I have your confirmation number?',
 'Hello? Are you there?','Sorry, can you hear me?']
BOTS=['Hi, I am Ava, your virtual assistant.',"I'm not a live person.",
 'Are you trying to add or update your known traveler number?',
 'I can give you a moment if you need it.']


def feed(seq, times=None, ask=None):
    t=HoldTracker(ask)
    return [t.feed(U(text,(times[i] if times else i*1000),turn_id=str(i))) for i,text in enumerate(seq)]


def scores():
    rows=[]
    boundaries=[('[music]',1000),('Your call is important, please hold.',1000)]
    boundaries += [(x,1000) for x in TRANSFERS]
    boundaries += [(None,8000)]
    for boundary,at in boundaries:
        for truth,texts in [('agent',AGENTS),('bot',BOTS)]:
            for text in texts:
                seq=[BOT]+([boundary] if boundary else [])+[text]
                times=[0,at,at+1000] if boundary else [0,at]
                r=feed(seq,times)[-1]
                rows.append(dict(boundary=boundary or '8000ms gap',truth=truth,text=text,label=r.label,confidence=r.confidence,evidence=r.evidence))
    for key in ('bot split name','bot explicit name'):
        r=feed(REVIEWER_CASES[key])[-1]
        rows.append(dict(boundary='none',truth='bot',text=REVIEWER_CASES[key][-1],label=r.label,confidence=r.confidence,evidence=r.evidence))
    return dict(rows=rows,agents=sum(r['truth']=='agent' for r in rows),bots=sum(r['truth']=='bot' for r in rows),
                bot_labels_on_agents=sum(r['truth']=='agent' and r['label']=='automated_assistant' for r in rows),
                false_humans=sum(r['truth']=='bot' and r['label']=='human' for r in rows),
                named_human_misses=sum(r['truth']=='agent' and r['boundary']!='8000ms gap' and r['text'] in AGENTS[:3] and r['label']!='human' for r in rows))


class PartyBoundaryTests(unittest.TestCase):
    def test_no_boundary_name_cannot_override_bot(self):
        r=feed(REVIEWER_CASES['bot split name'])[-1]
        self.assertEqual((r.label,r.confidence),('unknown','low'))
        self.assertIn('possible_new_party',r.evidence)

    def test_no_boundary_human_only_cues_are_uncertain(self):
        for text in AGENTS[-2:]:
            r=feed([BOT,text])[-1]
            self.assertEqual(r.label,'unknown');self.assertIn('possible_new_party',r.evidence)

    def test_zero_false_humans_and_zero_bot_labels_on_boundary_agents(self):
        r=scores()
        self.assertEqual(r['false_humans'],0)
        self.assertEqual(r['bot_labels_on_agents'],0)
        self.assertEqual(r['named_human_misses'],0)

    def test_exact_unnamed_boundary_contract(self):
        for prefix in ('[music]','Please hold.',*TRANSFERS):
            for text in AGENTS[3:]:
                r=feed([BOT,prefix,text])[-1]
                self.assertEqual((r.label,r.confidence),('unknown','low'))
                self.assertIn('possible_new_party',r.evidence)

    def test_exact_transfer_contract(self):
        for text in TRANSFERS:
            r=feed([BOT,text])[-1]
            self.assertEqual(r.label,'hold_message');self.assertIn('transfer',r.evidence)

    def test_silence_threshold_and_intervening_business_speech(self):
        for gap,expected in ((7999,'unknown'),(8000,'unknown'),(8001,'unknown')):
            r=feed([BOT,AGENTS[0]],[0,gap])[-1]
            self.assertEqual(r.label,expected)
        self.assertEqual(feed([BOT,'Hi.',AGENTS[0]],[0,7000,14000])[-1].label,'unknown')
        t=HoldTracker();t.feed(U(BOT,0));t.feed(U('Am I speaking with a live person?',7000,'hermes'))
        self.assertEqual(t.feed(U(AGENTS[0],8000)).label,'unknown')

    def test_filler_and_partial_speech_interrupt_silence(self):
        self.assertEqual(feed([BOT,'um',AGENTS[0]],[0,7000,14000])[-1].label,'unknown')
        t=HoldTracker();t.feed(U(BOT,0))
        t.feed(U('My name',7000,last=False,turn_id='partial'))
        self.assertEqual(t.feed(U(AGENTS[0],8000,turn_id='partial')).label,'unknown')
        t=HoldTracker();t.feed(U(BOT,0));t.feed(U('',7000))
        self.assertEqual(t.feed(U(AGENTS[0],8000)).label,'unknown')

    def test_same_reviewer_ten_second_timing_is_not_named_human_proof(self):
        # Round 8: silence alone no longer authorizes a named human after a bot.
        self.assertEqual(feed(REVIEWER_CASES['bot split name'],[0,10000])[-1].label,'unknown')

    def test_stateless_window_agrees_with_tracker(self):
        for prefix in TRANSFERS+['[music]','Please hold.']:
            window=[U(BOT,0),U(prefix,1000),U(AGENTS[4],2000)]
            r=classify(window)
            self.assertEqual(r.label,'unknown');self.assertIn('possible_new_party',r.evidence)

    def test_bot_declaration_after_boundary_resets_boundary(self):
        self.assertEqual(feed([BOT,'[music]',BOT,AGENTS[0]])[-1].label,'unknown')

    def test_repeated_possible_party_never_becomes_fake_hold_boundary(self):
        result=feed([BOT]+[AGENTS[0]]*20)
        for r in result[1:]:
            self.assertEqual(r.label,'unknown');self.assertIn('possible_new_party',r.evidence)

    def test_declaration_survives_window_and_hermes_turns(self):
        t=HoldTracker();t.feed(U(BOT,0))
        for i in range(20):t.feed(U('Routing detail '+str(i),i*1000,turn_id=str(i)))
        r=t.feed(U(AGENTS[0],21000));self.assertEqual(r.label,'unknown')
        self.assertIn('possible_new_party',r.evidence)

    def test_fresh_boundary_allows_same_named_agent_without_replaying_id(self):
        t=HoldTracker();t.feed(U('Please hold.',0,turn_id='h1'))
        self.assertEqual(t.feed(U(AGENTS[0],1000,turn_id='a1')).label,'human')
        t.feed(U('[music]',2000,turn_id='h2'))
        self.assertEqual(t.feed(U(AGENTS[0],3000,turn_id='a2')).label,'human')
        self.assertEqual(t.feed(U(AGENTS[0],4000,turn_id='a2')).label,'unknown')

    def test_possible_party_then_clear_confirmation(self):
        t=HoldTracker();t.feed(U(BOT,0));t.feed(U(AGENTS[0],1000))
        t.feed(U('Am I speaking with a live person?',2000,'hermes'))
        self.assertEqual(t.feed(U('Yes, I am a live person.',3000)).label,'human')

    def test_bot_declaration_wins_over_confirmation(self):
        t=HoldTracker();t.feed(U(BOT,0));t.feed(U(AGENTS[0],1000))
        t.feed(U('Am I speaking with a live person?',2000,'hermes'))
        self.assertEqual(t.feed(U("Yes, I'm your virtual assistant.",3000)).label,'automated_assistant')

    def test_model_cannot_override_possible_party(self):
        calls=[]
        def wrong(p):calls.append(p);return '{"label":"human","confidence":"high","evidence":[0,1],"contingent":true}'
        self.assertEqual(feed([BOT,AGENTS[0]],ask=wrong)[-1].label,'unknown')
        self.assertFalse(calls)

if __name__=='__main__':print(json.dumps(scores(),indent=2))
