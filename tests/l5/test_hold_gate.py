"""The hold gate: no voice-model session during IVR/hold; opens on a human; closes when
hold resumes, when the owner takes over and when the call ends."""
from __future__ import annotations

import unittest

from helpers import HOLD_MESSAGES, HUMAN_GREETING, Rig, run

from plugin.line.voice.phrases import PhraseCache
from plugin.line.voice.provider import Transcript

FORTY_MIN = 40 * 60


class HoldGateTests(unittest.TestCase):
    def test_forty_minute_hold_never_opens_a_voice_session(self):
        async def go():
            rig = Rig()
            await rig.start("dialing")
            await rig.state("ivr")
            await rig.state("on_hold")
            hold_start = rig.clock()
            await rig.hold_for(FORTY_MIN)
            hold_end = rig.clock()
            self.assertAlmostEqual(hold_end - hold_start, FORTY_MIN, places=3)
            # The fixture really ran: 120,000 frames of 20 ms reached the cheap path ...
            self.assertEqual(rig.stt.frames, FORTY_MIN * 50)
            self.assertEqual(rig.client.of("media"), [])          # ... and nothing was played back
            self.assertEqual(len(rig.port.prompts()), 40)          # hold messages went to L1/L2 as prompts
            self.assertIn(HOLD_MESSAGES[0], rig.port.prompts())
            # No provider session: never constructed, never connected, zero connected seconds.
            self.assertEqual(rig.hub.instances, [])
            self.assertEqual(rig.hub.connect_attempts, 0)
            self.assertEqual(rig.voice.connected_seconds, 0.0)
            self.assertEqual(rig.voice.meter.seconds_between(hold_start, hold_end), 0.0)
            self.assertEqual(rig.hub.audio_in, 0)
            await rig.close()
        run(go())

    def test_opens_on_human_and_streams_audio_in_conversation(self):
        async def go():
            rig = Rig()
            await rig.start("on_hold")
            await rig.hold_for(120)
            self.assertEqual(rig.hub.connect_attempts, 0)
            await rig.stt.utter(HUMAN_GREETING)
            await rig.state("human_detected")
            await rig.state("agent_talking")
            await rig.voice.begin_conversation("A live person answered: " + HUMAN_GREETING)
            await rig.flush()
            self.assertEqual(rig.hub.connect_attempts, 1)
            p = rig.hub.last
            self.assertTrue(p.connected)
            self.assertEqual(p.config.purpose, "conversation")
            self.assertEqual([t.name for t in p.config.tools][:2], ["ask_hermes", "bring_owner"])
            self.assertTrue(any(HUMAN_GREETING in c for c in p.contexts))  # Grok hears what was said
            self.assertEqual(p.responds[-1], None)                        # and answers right away
            self.assertIsNone(rig.stt.active)                             # the cheap ears stop
            for _ in range(10):
                rig.client.media(b"\x7f" * 160)
            await rig.flush()
            self.assertEqual(p.audio_in, 10)                              # µ-law passed straight through
            rig.clock.advance(300)
            self.assertAlmostEqual(rig.voice.connected_seconds, 300.0, places=3)
            await rig.close()
            self.assertFalse(p.connected)
        run(go())

    def test_closes_when_hold_resumes_by_state_tool_and_idle(self):
        async def go():
            # 1) L1 moves the call back to on_hold
            rig = Rig()
            await rig.start("agent_talking")
            await rig.voice.begin_conversation("person on the line")
            await rig.flush()
            self.assertTrue(rig.hub.last.connected)
            rig.clock.advance(30)
            await rig.state("on_hold")
            self.assertFalse(rig.hub.last.connected)
            self.assertIsNotNone(rig.stt.active)          # back on the cheap path
            rig.clock.advance(600)
            self.assertAlmostEqual(rig.voice.connected_seconds, 30.0, places=3)
            await rig.close()

            # 2) Grok calls wait_on_hold ("please hold, I'll transfer you")
            rig = Rig()
            await rig.start("agent_talking")
            await rig.voice.begin_conversation("person on the line")
            await rig.flush()
            rig.hub.last.tool_call("wait_on_hold", {})
            await rig.flush()
            self.assertIn("hold_resumed", rig.port.side_effects)
            self.assertFalse(rig.hub.last.connected)
            self.assertEqual(rig.voice.gate.state, "on_hold")
            self.assertEqual(rig.hub.last.responds, [None])   # no reply into the hold music
            await rig.close()

            # 3) nobody speaks for idle_hold_s in a conversation
            rig = Rig(settings={"idle_hold_s": 0.2})
            await rig.start("agent_talking")
            await rig.voice.begin_conversation("person on the line")
            await rig.flush()
            rig.clock.advance(1.0)
            import asyncio
            await asyncio.sleep(0.25)
            await rig.flush()
            self.assertIn("hold_resumed", rig.port.side_effects)
            self.assertFalse(rig.hub.last.connected)
            await rig.close()
        run(go())

    def test_reopens_for_a_second_human_after_hold(self):
        async def go():
            rig = Rig()
            await rig.start("agent_talking")
            await rig.voice.begin_conversation("first agent")
            await rig.flush()
            rig.clock.advance(60)
            await rig.state("on_hold")
            await rig.hold_for(600)
            await rig.state("human_detected")
            await rig.state("agent_talking")
            await rig.voice.begin_conversation("second agent after transfer")
            await rig.flush()
            self.assertEqual(rig.hub.connect_attempts, 2)
            self.assertTrue(rig.hub.last.connected)
            rig.clock.advance(60)
            self.assertAlmostEqual(rig.voice.connected_seconds, 120.0, places=3)
            await rig.close()
        run(go())

    def test_owner_takeover_and_call_end_close_everything(self):
        async def go():
            rig = Rig()
            await rig.start("on_hold")
            await rig.state("human_detected")           # owner mode: L1 says "please hold" and rings the owner
            await rig.voice.engine_frame({"type": "text", "token": "Thank you. Please hold for a moment.",
                                          "last": True})
            await rig.flush()
            p = rig.hub.last
            self.assertTrue(p.connected)
            await rig.state("ringing_owner")
            self.assertTrue(p.connected)                # still there while the owner's phone rings
            await rig.state("owner_connected")
            self.assertFalse(p.connected)
            self.assertIsNone(rig.stt.active)
            await rig.voice.engine_frame({"type": "end", "handoffData": "{\"reason\":\"takeover\"}"})
            await rig.flush()
            self.assertEqual(rig.client.closed_code, 1000)   # Twilio now requests the <Connect action> URL
            self.assertIn("voice_end", rig.port.event_kinds())
            await rig.close()
        run(go())

    def test_ivr_speech_opens_briefly_and_cached_lines_cost_nothing(self):
        async def go():
            phrases = PhraseCache()
            rig = Rig(phrases=phrases)
            await rig.start("ivr")
            await rig.voice.engine_frame({"type": "text", "token": "Reservations", "last": True})
            await rig.flush()
            p = rig.hub.last
            self.assertEqual(p.said, ["Reservations"])
            self.assertEqual(p.configs[0].purpose, "speech")
            self.assertEqual(p.configs[0].tools, [])
            self.assertFalse(p.connected)                  # closed right after rendering
            self.assertGreater(len(rig.client.of("media")), 0)
            self.assertEqual(p.audio_in, 0)                # caller audio never sent to a speech session
            self.assertIsNotNone(rig.stt.active)           # the cheap ears kept running
            # Same line on the next prompt (or the next call): played from cache, no session.
            await rig.voice.engine_frame({"type": "text", "token": "Reservations", "last": True})
            await rig.flush()
            self.assertEqual(rig.hub.connect_attempts, 1)
            self.assertEqual(rig.voice.counters["cached_lines"], 1)
            await rig.close()

            rig2 = Rig(phrases=phrases, welcome="Reservations")
            await rig2.start("dialing")
            self.assertEqual(rig2.hub.connect_attempts, 0)
            self.assertGreater(len(rig2.client.of("media")), 0)
            await rig2.close()
        run(go())

    def test_lines_with_digits_are_never_cached(self):
        async def go():
            phrases = PhraseCache()
            rig = Rig(phrases=phrases)
            await rig.start("ivr")
            await rig.voice.engine_frame({"type": "text", "token": "My confirmation is 48213", "last": True})
            await rig.flush()
            await rig.voice.engine_frame({"type": "text", "token": "My confirmation is 48213", "last": True})
            await rig.flush()
            self.assertEqual(rig.hub.connect_attempts, 2)
            await rig.close()
        run(go())

    def test_awaiting_approval_and_terminal_states_never_open(self):
        async def go():
            rig = Rig()
            await rig.start("awaiting_approval")
            await rig.voice.engine_frame({"type": "text", "token": "Hello there", "last": True})
            await rig.flush()
            self.assertEqual(rig.hub.connect_attempts, 0)
            await rig.state("ended")
            self.assertTrue(rig.voice.closed)
            await rig.voice.begin_conversation("late")
            await rig.flush()
            self.assertEqual(rig.hub.connect_attempts, 0)
            await rig.close()
        run(go())

    def test_transcripts_from_the_conversation_reach_l1(self):
        async def go():
            rig = Rig()
            await rig.start("agent_talking")
            await rig.voice.begin_conversation("x")
            await rig.flush()
            rig.hub.last.emit(Transcript("business", "We have a table at 7."))
            rig.hub.last.model_speaks("Great, please book it for four.")
            await rig.flush()
            self.assertIn(("business", "We have a table at 7."), rig.port.records)
            self.assertIn(("hermes", "Great, please book it for four."), rig.port.records)
            self.assertEqual(rig.port.prompts(), [])   # no Hermes text turns are started from them
            await rig.close()
        run(go())


if __name__ == "__main__":
    unittest.main()
