"""Findings from the first live Grok call (evidence/trial/call_374a992b53c044f9/trial.json).

These run the REAL Grok adapter (``GrokRealtimeProvider``) over a scripted xAI WebSocket
(``FakeWs``) inside a ``VoiceCall`` with a fake Twilio Media Stream, replaying event shapes
like the live call's:
1. the disclosure is spoken once and recorded once (Grok's transcript of a verbatim line is
   not a second line);
2. the caller transcript keeps one line per utterance from finals only: cumulative
   ``...input_audio_transcription.completed`` re-sends and ``.updated`` partials collapse;
3. barge-in on the Grok path: ``speech_started`` while audio plays -> Twilio ``clear`` +
   ``conversation.item.truncate`` on the wire + ``voice_barge_in``.
"""
from __future__ import annotations

import asyncio
import json
import unittest

from helpers import Rig, run

from plugin.line.voice import mulaw
from plugin.line.voice.grok import GrokRealtimeProvider
from plugin.line.voice.testing import FakeWs, fake_connector

KEY = "xai-synthetic-test-key"
DISCLOSURE = "Hi, this is Hermes, an AI assistant calling on behalf of Can. This call is not recorded."


def xai_like(msg):
    """Server replies as observed live: session.updated; a force_message renders as a full
    response with audio deltas and an output transcript."""
    kind = msg.get("type")
    if kind == "session.update":
        return [{"type": "session.updated", "session": {}}]
    if kind == "conversation.item.create" and msg["item"]["type"] == "force_message":
        text = msg["item"]["content"][0]["text"]
        chunk = mulaw.b64(mulaw.tone([300], 200))
        return ([{"type": "response.created", "response": {"id": "r_fm"}}]
                + [{"type": "response.output_audio.delta", "delta": chunk, "item_id": "i_fm", "response_id": "r_fm"}] * 3
                + [{"type": "response.output_audio_transcript.done", "transcript": text},
                   {"type": "response.done", "response": {"id": "r_fm", "status": "completed"}}])
    return []


class WireRig(Rig):
    """A Rig whose provider is the real Grok adapter on scripted sockets."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.sockets = []

        def factory():
            ws = FakeWs(xai_like)
            self.sockets.append(ws)
            return GrokRealtimeProvider(lambda n: KEY, connector=fake_connector(ws))
        self.voice._provider_factory = factory

    @property
    def ws(self) -> FakeWs:
        return self.sockets[-1]

    def sent(self, kind):
        return [m for ws in self.sockets for m in ws.sent_json() if m.get("type") == kind]

    def business(self):
        return [t for s, t in self.port.records if s == "business"]

    def hermes(self):
        return [t for s, t in self.port.records if s == "hermes"]


def completed(text, item="item_u1"):
    return {"type": "conversation.item.input_audio_transcription.completed", "item_id": item, "transcript": text}


def updated(text, item="item_u1"):
    return {"type": "conversation.item.input_audio_transcription.updated", "item_id": item, "transcript": text}


async def talking(rig: WireRig) -> None:
    await rig.start("agent_talking")
    await rig.voice.begin_conversation("A person answered.")
    await rig.flush()


class DisclosureOnceTests(unittest.TestCase):
    def test_disclosure_is_spoken_once_and_not_recorded_again_from_grok(self):
        async def go():
            rig = WireRig(welcome=DISCLOSURE)
            await rig.start("dialing")
            await rig.flush()
            forced = [m for m in rig.sent("conversation.item.create") if m["item"]["type"] == "force_message"]
            self.assertEqual([m["item"]["content"][0]["text"] for m in forced], [DISCLOSURE])   # spoken once
            self.assertEqual(len(rig.client.of("media")), 3)
            # L1 writes the disclosure line itself at setup; the voice adds none (the live call had two).
            self.assertEqual(rig.hermes(), [])
            self.assertIsNone(rig.voice.provider)                        # rendered, then closed (still dialing)
            # a second Twilio stream on the same call (reconnect) does not speak it again
            from plugin.line.voice.testing import FakeMediaStreamsClient
            rig.client.hang_up()
            await rig.flush()
            rig.client = FakeMediaStreamsClient(stream_sid="MZ" + "4" * 32)
            rig.serve_task = asyncio.get_running_loop().create_task(rig.voice.serve(rig.client.socket))
            rig.client.start()
            await rig.flush()
            forced = [m for m in rig.sent("conversation.item.create") if m["item"]["type"] == "force_message"]
            self.assertEqual(len(forced), 1)
            await rig.close()
        run(go())

    def test_grok_speech_is_recorded_but_verbatim_lines_only_by_their_owner(self):
        async def go():
            rig = WireRig(settings={"filler_after_s": 0.05})
            await talking(rig)
            rig.ws.push({"type": "response.created", "response": {"id": "r1"}})
            rig.ws.push({"type": "response.output_audio_transcript.done", "transcript": "Doing great, thanks."})
            rig.ws.push({"type": "response.done", "response": {"id": "r1", "status": "completed"}})
            await rig.flush()
            await rig.voice.engine_frame({"type": "text", "token": "Can says hello.", "last": True})   # L1's line
            rig.port.ask_delay = 0.2
            rig.ws.push({"type": "response.function_call_arguments.done", "call_id": "c1", "name": "ask_hermes",
                         "arguments": json.dumps({"question": "status?"})})
            await asyncio.sleep(0.3)
            await rig.flush()
            self.assertEqual(rig.hermes(), ["Doing great, thanks.", "One moment please, I'm still checking."])
            await rig.close()
        run(go())


class TranscriptTests(unittest.TestCase):
    def test_cumulative_finals_and_partials_become_one_line(self):
        async def go():
            rig = WireRig()
            await talking(rig)
            for text in ("I can hear you.", "I can hear you clearly, but",
                         "I can hear you clearly, but can you tell me how"):
                rig.ws.push(updated(text, "item_9"))
                rig.ws.push(completed(text, "item_9"))
            final = "I can hear you clearly, but can you tell me how's my BB agents are doing right now?"
            rig.ws.push(completed(final, "item_9"))
            rig.ws.push(completed(final, "item_9"))                      # the repeated final
            await rig.flush()
            self.assertEqual(rig.business(), [])                         # nothing recorded mid-utterance
            rig.ws.push({"type": "response.created", "response": {"id": "r2"}})   # Grok starts to answer
            await rig.flush()
            self.assertEqual(rig.business(), [final])
            rig.ws.push(completed(final, "item_9"))                      # a late duplicate during the answer
            await rig.flush()
            self.assertEqual(rig.business(), [final])
            await rig.close()
        run(go())

    def test_growing_finals_without_item_ids_and_duplicate_finals(self):
        async def go():
            rig = WireRig()
            await talking(rig)
            for text in ("Good. How", "Good, how are you doing?", "Good, how are you doing?"):
                rig.ws.push(completed(text, ""))
            rig.ws.push({"type": "response.created", "response": {"id": "r3"}})
            rig.ws.push({"type": "response.output_audio_transcript.done", "transcript": "Doing great."})
            for text in ("No.", "No, I'm fine.", "No, I'm fine."):
                rig.ws.push(completed(text, f"item_{text}"))             # even with different item ids
            rig.ws.push({"type": "response.created", "response": {"id": "r4"}})
            await rig.flush()
            self.assertEqual(rig.business(), ["Good, how are you doing?", "No, I'm fine."])
            await rig.close()
        run(go())

    def test_a_real_repeat_in_a_later_turn_is_kept_and_quiet_lines_settle(self):
        async def go():
            rig = WireRig(settings={"transcript_settle_s": 0.05})
            await talking(rig)
            rig.ws.push(completed("Yes.", "a"))
            rig.ws.push({"type": "response.created", "response": {"id": "r5"}})
            rig.ws.push({"type": "response.output_audio_transcript.done", "transcript": "For four people?"})
            rig.ws.push(completed("Yes.", "b"))                          # no response follows: settles
            await asyncio.sleep(0.1)
            await rig.flush()
            self.assertEqual(rig.business(), ["Yes.", "Yes."])
            await rig.close()
        run(go())


class BargeInWireTests(unittest.TestCase):
    def test_speech_started_while_grok_plays_clears_twilio_and_truncates_on_the_wire(self):
        async def go():
            rig = WireRig()
            await talking(rig)
            chunk = mulaw.b64(mulaw.tone([300], 100))                    # 100 ms = 800 bytes per delta
            rig.ws.push({"type": "response.created", "response": {"id": "r6"}})
            for _ in range(5):
                rig.ws.push({"type": "response.output_audio.delta", "delta": chunk, "item_id": "item_A",
                             "response_id": "r6"})
            await rig.flush()
            marks = list(rig.client.outstanding)
            self.assertEqual(len(marks), 5)
            rig.client.outstanding = marks[2:]                           # Twilio played 2 deltas (200 ms)
            for name in marks[:2]:
                rig.client.send({"event": "mark", "streamSid": rig.client.stream_sid, "mark": {"name": name}})
            await rig.flush()
            rig.ws.push({"type": "input_audio_buffer.speech_started", "item_id": "item_user"})
            await rig.flush()
            self.assertEqual(len(rig.client.of("clear")), 1)
            self.assertEqual(rig.sent("conversation.item.truncate"),
                             [{"type": "conversation.item.truncate", "item_id": "item_A", "content_index": 0,
                               "audio_end_ms": 200}])
            kinds = rig.port.event_kinds()
            self.assertIn("voice_barge_in", kinds)                     # what the trial harness collects
            self.assertIn(("voice_speech_started", {"playing": True}), rig.port.events)
            await rig.close()
        run(go())

    def test_speech_started_with_nothing_playing_is_logged_not_cleared(self):
        async def go():
            rig = WireRig(auto_ack=True)
            await talking(rig)
            rig.ws.push({"type": "input_audio_buffer.speech_started", "item_id": "u"})
            await rig.flush()
            self.assertEqual(rig.client.of("clear"), [])
            self.assertIn(("voice_speech_started", {"playing": False}), rig.port.events)
            self.assertNotIn("voice_barge_in", rig.port.event_kinds())
            await rig.close()
        run(go())

    def test_a_partial_while_playing_also_barges_in(self):
        async def go():
            rig = WireRig()
            await talking(rig)
            rig.ws.push({"type": "response.output_audio.delta", "delta": mulaw.b64(mulaw.tone([300], 100)),
                         "item_id": "item_B", "response_id": "r7"})
            await rig.flush()
            rig.ws.push(updated("Sorry, wait", "u2"))
            await rig.flush()
            self.assertEqual(len(rig.client.of("clear")), 1)
            self.assertEqual(rig.business(), [])                         # a partial is never a line
            await rig.close()
        run(go())


if __name__ == "__main__":
    unittest.main()
