"""Conversation behaviour: barge-in, tool rules (zero side effects when rejected), the
ask_hermes latency budget, sealed codes, and provider disconnects."""
from __future__ import annotations

import asyncio
import json
import unittest

from helpers import Rig, run

from plugin.line.voice import mulaw
from plugin.line.voice.controller import FILLER
from plugin.line.voice.phrases import DIGIT_WORDS, PhraseCache
from plugin.line.voice.provider import SpeechStarted
from plugin.line.voice.testing import synthetic_speech


class SecretText:
    """Shape of line.sms.SecretText for these tests (the real one is used in the L1 rehearsal)."""

    def __init__(self, value: str) -> None:
        self._v = value

    def reveal(self) -> str:
        return self._v

    def __repr__(self) -> str:
        return "SecretText(***)"


async def conversation(rig: Rig) -> None:
    await rig.start("agent_talking")
    await rig.voice.begin_conversation("A person answered.")
    await rig.flush()


def outputs(rig: Rig):
    return [r["output"] for r in rig.hub.last.tool_results]


class BargeInTests(unittest.TestCase):
    def test_barge_in_clears_twilio_playback_and_truncates_what_was_heard(self):
        async def go():
            rig = Rig()
            await conversation(rig)
            p = rig.hub.last
            p.model_speaks("Hello, I am calling to book a table for four people on Friday evening at seven.",
                           item="item_A")
            await rig.flush()
            marks = list(rig.client.outstanding)
            self.assertGreater(len(marks), 3)
            self.assertTrue(rig.voice.bridge.playing)
            # Twilio played the first two chunks, then the person starts talking.
            played = marks[:2]
            rig.client.outstanding = marks[2:]
            for name in played:
                rig.client.send({"event": "mark", "streamSid": rig.client.stream_sid, "mark": {"name": name}})
            await rig.flush()
            p.emit(SpeechStarted("item_user"))
            await rig.flush()
            self.assertEqual(len(rig.client.of("clear")), 1)
            self.assertFalse(rig.voice.bridge.playing)
            item, ms = p.truncates[-1]
            self.assertEqual(item, "item_A")
            # 2 chunks of 5 x 20 ms were heard: 200 ms.
            self.assertEqual(ms, 200)
            self.assertEqual(rig.voice.counters["barge_ins"], 1)
            await rig.close()
        run(go())

    def test_no_clear_when_nothing_is_playing(self):
        async def go():
            rig = Rig(auto_ack=True)
            await conversation(rig)
            rig.hub.last.model_speaks("Short.")
            await rig.flush()
            rig.hub.last.emit(SpeechStarted("u"))
            await rig.flush()
            self.assertEqual(rig.client.of("clear"), [])
            await rig.close()
        run(go())

    def test_hermes_text_mode_barge_in_interrupts_the_hermes_turn(self):
        async def go():
            rig = Rig(settings={"conversation": "hermes_text"})
            await rig.start("agent_talking")
            await rig.voice.engine_frame({"type": "text", "token": "Hi! I'd like to book a table. ", "last": False})
            await rig.voice.engine_frame({"type": "text", "token": "For four at seven.", "last": True})
            await rig.flush()
            self.assertEqual(rig.hub.last.said, ["Hi! I'd like to book a table.", "For four at seven."])
            self.assertTrue(rig.voice.bridge.playing)
            await rig.stt.utter("Sorry, which day?")
            await rig.flush()
            self.assertEqual(len(rig.client.of("clear")), 1)
            self.assertIn({"type": "interrupt", "utteranceUntilInterrupt": None}, rig.port.offers)
            self.assertEqual(rig.port.prompts()[-1], "Sorry, which day?")
            await rig.close()
        run(go())


class ToolRuleTests(unittest.TestCase):
    def test_ask_hermes_answers_from_hermes(self):
        async def go():
            rig = Rig()
            await conversation(rig)
            rig.hub.last.tool_call("ask_hermes", {"question": "Is the owner free Friday at 7?"})
            await rig.flush()
            self.assertEqual(rig.port.asks, ["Is the owner free Friday at 7?"])
            out = outputs(rig)[-1]
            self.assertEqual(out, {"ok": True, "answer": "Yes, the owner is free on Friday at 7 pm."})
            self.assertEqual(rig.hub.last.responds, [None, None])   # conversation start + after the tool
            await rig.close()
        run(go())

    def test_slow_hermes_gets_a_filler_then_a_timeout_answer(self):
        async def go():
            rig = Rig(settings={"filler_after_s": 0.05, "ask_timeout_s": 0.2})
            await conversation(rig)
            rig.port.ask_delay = 5.0
            rig.hub.last.tool_call("ask_hermes", {"question": "Check the calendar"})
            await asyncio.sleep(0.4)
            await rig.flush()
            self.assertIn(FILLER, rig.hub.last.said)
            out = outputs(rig)[-1]
            self.assertFalse(out["ok"])
            self.assertIn("did not answer in time", out["error"])
            await rig.close()
        run(go())

    def test_rejected_tool_calls_have_no_side_effects(self):
        async def go():
            rig = Rig()
            await conversation(rig)
            rig.port.owner_allowed = False
            p = rig.hub.last
            p.tool_call("bring_owner", {})                                   # no owner approval/grant
            p.tool_call("send_verification_code", {"mode": "speak"})          # no approved code
            p.tool_call("press_keys", {"digits": "911; transfer"})            # bad digits
            p.tool_call("press_keys", {"digits": "1", "to": "+15550100"})     # smuggled argument
            p.tool_call("transfer_call", {"number": "+18005550100"})          # not a tool
            p.tool_call("report_outcome", {"outcome": "approved", "summary": ""})
            p.emit(__import__("plugin.line.voice.provider", fromlist=["ToolCall"]).ToolCall("c9", "ask_hermes", "not json"))
            await rig.flush()
            outs = outputs(rig)
            self.assertEqual(len(outs), 7)
            self.assertTrue(all(o["ok"] is False for o in outs), outs)
            self.assertEqual(rig.port.side_effects, [])
            self.assertIsNone(rig.port.outcome)
            self.assertEqual(rig.port.asks, [])
            self.assertEqual(rig.client.of("media"), [])                      # no tones, no code audio
            self.assertEqual(rig.voice.router.rejected, 7)
            await rig.close()
        run(go())

    def test_tools_are_refused_outside_a_conversation(self):
        async def go():
            rig = Rig()
            await rig.start("ivr")
            await rig.voice.engine_frame({"type": "text", "token": "Reservations please", "last": True})
            p = rig.hub.last
            p.tool_call("bring_owner", {})
            p.tool_call("ask_hermes", {"question": "anything"})
            await rig.flush()
            self.assertEqual(rig.voice.router.rejected, 2)
            self.assertEqual(rig.port.side_effects, [])
            self.assertEqual(rig.port.asks, [])
            await rig.close()
        run(go())

    def test_tool_budget_and_single_flight(self):
        async def go():
            rig = Rig(settings={"max_tool_calls_per_minute": 3})
            await conversation(rig)
            rig.port.ask_delay = 0.05
            p = rig.hub.last
            p.tool_call("ask_hermes", {"question": "a"}, call_id="c1")
            p.tool_call("ask_hermes", {"question": "b"}, call_id="c2")
            await rig.flush()
            res = {r["call_id"]: r["output"] for r in p.tool_results}
            self.assertTrue(res["c1"]["ok"])
            self.assertIn("already running", res["c2"]["error"])
            for i in range(3):
                p.tool_call("report_outcome", {"outcome": "done", "summary": f"s{i}"}, call_id=f"r{i}")
            await rig.flush()
            res = {r["call_id"]: r["output"] for r in p.tool_results}
            self.assertTrue(res["r0"]["ok"])
            self.assertIn("too many", res["r2"]["error"])
            await rig.close()
        run(go())

    def test_only_the_last_parallel_tool_result_asks_for_a_reply(self):
        async def go():
            rig = Rig()
            await conversation(rig)
            rig.port.ask_delay = 0.05
            p = rig.hub.last
            p.tool_call("report_outcome", {"outcome": "done", "summary": "Booked."}, call_id="a")
            p.tool_call("ask_hermes", {"question": "anything else?"}, call_id="b")
            await asyncio.sleep(0.1)
            await rig.flush()
            self.assertEqual([r["call_id"] for r in p.tool_results], ["a", "b"])
            self.assertEqual(p.responds, [None, None])   # one reply, after both outputs are in
            await rig.close()
        run(go())

    def test_end_call_waits_for_the_goodbye_to_play(self):
        async def go():
            rig = Rig(settings={"end_drain_s": 2.0})
            await conversation(rig)
            p = rig.hub.last
            p.tool_call("report_outcome", {"outcome": "done", "summary": "Table for 4, Friday 7 pm."})
            p.tool_call("end_call", {})
            await rig.flush()
            self.assertEqual(rig.port.outcome, ("done", "Table for 4, Friday 7 pm."))
            p.model_speaks("Thank you, goodbye!")
            await rig.flush()
            self.assertNotIn("end_call", rig.port.side_effects)        # goodbye still playing
            rig.client.ack_marks()
            await rig.flush()
            self.assertEqual(rig.port.side_effects, ["end_call"])
            self.assertFalse(p.connected)
            await rig.close()
        run(go())

    def test_tool_results_are_masked(self):
        async def go():
            rig = Rig()
            rig.voice.router.mask = lambda s: s.replace("4111 1111 1111 1111", "[CARD]")
            await conversation(rig)
            rig.port.ask_answer = "Use card 4111 1111 1111 1111"
            rig.hub.last.tool_call("ask_hermes", {"question": "Which card?"})
            await rig.flush()
            self.assertEqual(outputs(rig)[-1]["answer"], "Use card [CARD]")
            await rig.close()
        run(go())


class TimingEventTests(unittest.TestCase):
    def test_live_plan_timings_are_logged(self):
        async def go():
            rig = Rig()
            await conversation(rig)
            rig.hub.last.model_speaks("Hello there.")
            await rig.flush()
            events = dict((k, d) for k, d in rig.port.events if k in ("voice_open", "voice_first_audio"))
            self.assertIn("connect_ms", events["voice_open"])
            self.assertGreaterEqual(events["voice_first_audio"]["ms"], 0)
            self.assertEqual(rig.port.event_kinds().count("voice_first_audio"), 1)
            await rig.close()
        run(go())


class SealedCodeTests(unittest.TestCase):
    CODE = "482913"

    def _assert_never_sent(self, rig: Rig) -> None:
        for text in rig.hub.all_text_sent:
            self.assertNotIn(self.CODE, text.replace(" ", ""))
            self.assertNotIn(" ".join(self.CODE), text)

    def test_dtmf_code_goes_as_local_tones_never_to_the_model(self):
        async def go():
            rig = Rig()
            await conversation(rig)
            ok = await rig.voice.send_sealed(SecretText(self.CODE), "dtmf")
            await rig.flush()
            self.assertTrue(ok)
            audio = rig.client.audio_out()
            self.assertEqual(len(audio), len(mulaw.dtmf_tones(self.CODE)))
            self._assert_never_sent(rig)
            self.assertEqual(rig.hub.last.said, [])
            await rig.close()
        run(go())

    def test_spoken_code_uses_cached_digit_clips_or_is_refused(self):
        async def go():
            rig = Rig()
            await conversation(rig)
            self.assertFalse(await rig.voice.send_sealed(SecretText(self.CODE), "speak"))   # no clips yet
            # L1's current sealed path renders the code as spaced characters on a text frame:
            await rig.voice.engine_frame({"type": "text", "token": " ".join(self.CODE), "last": True})
            await rig.flush()
            self.assertEqual(rig.voice.counters["sealed_refused"], 2)
            self.assertEqual(rig.client.of("media"), [])
            self._assert_never_sent(rig)
            await rig.close()

            phrases = PhraseCache()
            rig = Rig(phrases=phrases)
            await rig.start("on_hold")
            await rig.voice.prepare_sealed_speech()          # owner approved a code for this call
            await rig.flush()
            self.assertEqual(rig.hub.last.said, list(DIGIT_WORDS))
            self.assertEqual(rig.client.of("media"), [])     # rendered, not played on the call
            self.assertFalse(rig.hub.last.connected)         # and the session closed again
            self.assertIsNotNone(phrases.digit_clips("eve"))
            await rig.state("agent_talking")
            await rig.voice.begin_conversation("x")
            await rig.flush()
            self.assertTrue(await rig.voice.send_sealed(SecretText(self.CODE), "speak"))
            await rig.flush()
            expected = b"".join(synthetic_speech(DIGIT_WORDS[int(d)]) + mulaw.silence(250) for d in self.CODE)
            self.assertEqual(rig.client.audio_out(), expected)
            self._assert_never_sent(rig)
            await rig.close()
        run(go())

    def test_bad_secrets_are_refused(self):
        async def go():
            rig = Rig()
            await conversation(rig)
            self.assertFalse(await rig.voice.send_sealed(self.CODE, "dtmf"))              # plain str
            self.assertFalse(await rig.voice.send_sealed(SecretText("12"), "dtmf"))       # too short
            self.assertFalse(await rig.voice.send_sealed(SecretText("ab12cd"), "dtmf"))   # letters as tones
            self.assertFalse(await rig.voice.send_sealed(SecretText("1234"), "sms"))
            await rig.flush()
            self.assertEqual(rig.client.of("media"), [])
            await rig.close()
        run(go())


class RecoveryTests(unittest.TestCase):
    def test_provider_drop_reconnects_with_context(self):
        async def go():
            rig = Rig()
            await conversation(rig)
            first = rig.hub.last
            first.model_speaks("We'd like a table for four.")
            await rig.flush()
            rig.clock.advance(20)
            first.drop()
            await asyncio.sleep(0.05)
            await rig.flush()
            self.assertEqual(len(rig.hub.instances), 2)
            second = rig.hub.last
            self.assertTrue(second.connected)
            self.assertEqual(second.config.purpose, "conversation")
            self.assertTrue(any("table for four" in c for c in second.contexts))
            self.assertIn("dropped", second.responds[-1])
            self.assertEqual(rig.voice.counters["reconnects"], 1)
            self.assertEqual(rig.port.failed, [])
            rig.clock.advance(10)
            self.assertAlmostEqual(rig.voice.connected_seconds, 30.0, places=3)
            await rig.close()
        run(go())

    def test_provider_drop_without_recovery_ends_cleanly(self):
        async def go():
            rig = Rig(settings={"max_reconnects": 2})
            await conversation(rig)
            rig.hub.fail_connects = 5
            rig.clock.advance(15)
            rig.hub.last.drop()
            await asyncio.sleep(0.1)
            await rig.flush()
            self.assertEqual(rig.hub.connect_attempts, 3)            # first + 2 retries
            self.assertEqual(rig.port.failed, ["the voice connection was lost"])
            self.assertTrue(rig.voice.closed)                       # L1 ended the call (port -> failed)
            self.assertIsNone(rig.voice.provider)
            self.assertIsNone(rig.stt.active)
            rig.clock.advance(100)
            self.assertAlmostEqual(rig.voice.connected_seconds, 15.0, places=3)
            self.assertEqual(rig.hub.connected_now, 0)
            await rig.close()
        run(go())

    def test_drop_while_rendering_a_line_rerenders_it(self):
        async def go():
            rig = Rig(settings={"max_reconnects": 1})
            await rig.start("ivr")
            rig.hub.drop_on_say = 1
            await rig.voice.engine_frame({"type": "text", "token": "Reservations please", "last": True})
            await asyncio.sleep(0.05)
            await rig.flush()
            self.assertEqual(len(rig.hub.instances), 2)
            self.assertEqual(rig.hub.last.said, ["Reservations please"])
            self.assertGreater(len(rig.client.of("media")), 0)
            self.assertFalse(rig.hub.last.connected)     # rendered, then closed again (still IVR)
            self.assertEqual(rig.port.failed, [])
            await rig.close()
        run(go())

    def test_no_key_at_human_time_fails_the_voice_cleanly(self):
        async def go():
            rig = Rig()
            rig.hub.fail_connects = 1
            await rig.start("on_hold")
            await rig.state("agent_talking")
            await rig.flush()
            self.assertEqual(rig.port.failed, ["the voice provider is unavailable"])
            await rig.close()
        run(go())

    def test_stream_drop_closes_the_session_and_a_new_stream_resumes(self):
        async def go():
            rig = Rig()
            await conversation(rig)
            self.assertTrue(rig.hub.last.connected)
            rig.client.hang_up()                       # Twilio's socket went away
            await rig.flush()
            self.assertFalse(rig.hub.last.connected)
            self.assertIsNone(rig.voice.bridge)
            from plugin.line.voice.testing import FakeMediaStreamsClient
            rig.client = FakeMediaStreamsClient(stream_sid="MZ" + "3" * 32)
            rig.serve_task = asyncio.get_running_loop().create_task(rig.voice.serve(rig.client.socket))
            rig.client.start()
            await rig.flush()
            self.assertEqual(len(rig.hub.instances), 2)
            self.assertTrue(rig.hub.last.connected)
            self.assertEqual(rig.voice.counters["streams"], 2)
            await rig.close()
        run(go())


if __name__ == "__main__":
    unittest.main()
