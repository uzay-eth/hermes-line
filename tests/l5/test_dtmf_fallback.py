"""Menu keys: in-band tones first, REST-redirect fallback when the menu does not react,
and verification-code digits refused on the menu path and on the fallback."""
from __future__ import annotations

import asyncio
import unittest

from helpers import Rig, run

from plugin.line.voice import mulaw
from plugin.line.voice.dtmf import DigitsRefused, DtmfFallback, SealedGuard, menu_did_not_react
from plugin.line.voice.testing import VirtualClock
from plugin.line.voice.twiml import play_digits_then_stream, response

PROMPT = "For reservations, press 1. For hours and directions, press 2. To hear these options again, press 9."
CODE = "482913"


class SecretText:
    def __init__(self, v):
        self._v = v

    def reveal(self):
        return self._v


class FallbackPort:
    """Adds L1's side of the fallback to the fake port: redirect the live leg to the digits
    URL, then (as Twilio fetching that URL) take the digits and build the TwiML."""

    def __init__(self, rig: Rig, *, ok: bool = True) -> None:
        self.rig = rig
        self.ok = ok
        self.redirects = []
        self.documents = []
        rig.port.redirect_digits = self.redirect_digits

    async def redirect_digits(self, nonce: str):
        if not self.ok:
            raise RuntimeError("Twilio HTTP 500")
        self.redirects.append(nonce)
        digits = self.rig.voice.take_redirect_digits(nonce)       # Twilio fetches the URL once
        if digits is None:
            self.documents.append(None)
            return {"ok": False, "error": "plan gone"}
        self.documents.append(response(play_digits_then_stream(
            digits=digits, ws_url="wss://line.test/t/SECRET/twilio/media/call_test/FRESHTOKEN",
            action_url="https://line.test/t/SECRET/twilio/voice/connect/action/call_test", call_id="call_test")))
        return {"ok": True}


async def ivr(rig: Rig) -> None:
    await rig.start("ivr")
    await rig.stt.utter(PROMPT)
    await rig.flush()


async def press_and_play(rig: Rig, digits: str):
    out = await rig.voice.press_menu(digits)
    await rig.flush()
    rig.client.ack_marks()          # the tones finished playing on the call
    await rig.flush()
    return out


class PolicyTests(unittest.TestCase):
    def test_tones_first_and_no_fallback_when_the_menu_moves_on(self):
        async def go():
            rig = Rig()
            fb = FallbackPort(rig)
            await ivr(rig)
            out = await press_and_play(rig, "1")
            self.assertEqual(out, {"ok": True, "via": "tones"})
            self.assertEqual(rig.client.audio_out(), mulaw.dtmf_tones("1"))
            await rig.stt.utter("Reservations. Please hold while we connect you.")
            await rig.flush()
            self.assertEqual(fb.redirects, [])
            self.assertEqual(rig.voice.counters["dtmf_redirects"], 0)
            self.assertEqual(rig.port.prompts()[-1], "Reservations. Please hold while we connect you.")
            await rig.close()
        run(go())

    def test_repeated_prompt_triggers_one_redirect_and_later_presses_use_it(self):
        async def go():
            rig = Rig()
            fb = FallbackPort(rig)
            await ivr(rig)
            await press_and_play(rig, "1")
            prompts_before = len(rig.port.prompts())
            await rig.stt.utter(PROMPT)                     # the menu ignored the tones
            await rig.flush()
            self.assertEqual(len(fb.redirects), 1)
            doc = fb.documents[0]
            self.assertIn('<Play digits="1"/>', doc)
            self.assertIn('<Stream url="wss://line.test/t/SECRET/twilio/media/call_test/FRESHTOKEN">', doc)
            self.assertLess(doc.index("<Play"), doc.index("<Connect"))
            self.assertGreaterEqual(len(fb.redirects[0]), 24)              # the URL carries a random nonce only
            self.assertIsNone(rig.voice.take_redirect_digits(fb.redirects[0]))   # single use
            # the repeated prompt reached the transcript but not Hermes (no double press)
            self.assertEqual(len(rig.port.prompts()), prompts_before)
            self.assertIn(("business", PROMPT), rig.port.records)
            # next press on this call goes straight to the fallback, no tones
            audio_before = len(rig.client.audio_out())
            out = await rig.voice.press_menu("2")
            self.assertEqual(out, {"ok": True, "via": "redirect"})
            self.assertEqual(len(fb.redirects), 2)
            self.assertIn('<Play digits="2"/>', fb.documents[1])
            self.assertEqual(len(rig.client.audio_out()), audio_before)
            await rig.close()
        run(go())

    def test_split_prompt_and_partial_repeat_trigger_the_fallback(self):
        async def go():
            rig = Rig()
            fb = FallbackPort(rig)
            await rig.start("ivr")
            for sentence in PROMPT.split(". "):              # STT delivered the menu in three pieces
                await rig.stt.utter(sentence)
                await rig.flush()
            await press_and_play(rig, "1")
            await rig.stt.utter("For reservations, press 1.")   # the menu starts over
            await rig.flush()
            self.assertEqual(len(fb.redirects), 1)
            await rig.close()
        run(go())

    def test_error_phrase_triggers_the_fallback(self):
        async def go():
            rig = Rig()
            fb = FallbackPort(rig)
            await ivr(rig)
            await press_and_play(rig, "1")
            await rig.stt.utter("I'm sorry, I didn't get that.")
            await rig.flush()
            self.assertEqual(len(fb.redirects), 1)
            await rig.close()
        run(go())

    def test_no_decision_while_tones_play_or_after_the_window(self):
        async def go():
            rig = Rig(settings={"dtmf_react_window_s": 8.0})
            fb = FallbackPort(rig)
            await ivr(rig)
            await rig.voice.press_menu("1")
            await rig.flush()
            await rig.stt.utter(PROMPT)                      # tones not acknowledged yet: ignored
            await rig.flush()
            self.assertEqual(fb.redirects, [])
            rig.client.ack_marks()
            await rig.flush()
            rig.clock.advance(9.0)                           # silence past the window is not a signal
            await rig.stt.utter(PROMPT)
            await rig.flush()
            self.assertEqual(fb.redirects, [])
            await rig.close()
        run(go())

    def test_fallback_is_tried_once_per_press(self):
        async def go():
            rig = Rig()
            fb = FallbackPort(rig, ok=False)
            await ivr(rig)
            await press_and_play(rig, "1")
            await rig.stt.utter(PROMPT)
            await rig.flush()
            await rig.stt.utter(PROMPT)
            await rig.flush()
            self.assertIn("voice_dtmf_fallback_failed", rig.port.event_kinds())
            self.assertEqual(rig.port.event_kinds().count("voice_dtmf_no_reaction"), 1)
            self.assertFalse(rig.voice._redirect_mode)
            self.assertEqual(rig.voice.dtmf._plans, {})     # the failed plan was discarded
            await rig.close()
        run(go())

    def test_relay_frame_digits_never_use_the_fallback(self):
        async def go():
            rig = Rig()
            fb = FallbackPort(rig)
            await ivr(rig)
            await rig.voice.engine_frame({"type": "sendDigits", "digits": "1"})
            await rig.flush()
            rig.client.ack_marks()
            await rig.flush()
            await rig.stt.utter(PROMPT)
            await rig.flush()
            self.assertEqual(fb.redirects, [])
            self.assertEqual(rig.client.audio_out(), mulaw.dtmf_tones("1"))
            await rig.close()
        run(go())

    def test_port_without_fallback(self):
        async def go():
            rig = Rig()
            await ivr(rig)
            await press_and_play(rig, "1")
            await rig.stt.utter(PROMPT)
            await rig.flush()
            self.assertIn("voice_dtmf_fallback_unavailable", rig.port.event_kinds())
            await rig.close()
        run(go())


class CodeDigitsTests(unittest.TestCase):
    def test_code_digits_refused_on_the_fallback(self):
        async def go():
            rig = Rig()
            fb = FallbackPort(rig)
            await ivr(rig)
            self.assertTrue(await rig.voice.send_sealed(SecretText(CODE), "dtmf"))   # the sealed path: tones
            await rig.flush()
            rig.client.ack_marks()
            await rig.flush()
            sealed_audio = rig.client.audio_out()
            # After the code is out, the menu path refuses it in any form, tones or fallback.
            rig.voice._redirect_mode = True
            for digits in (CODE, "1" + CODE + "#", "48w2913", CODE[:3] + "w" + CODE[3:]):
                out = await rig.voice.press_menu(digits)
                self.assertFalse(out["ok"], digits)
                self.assertIn("verification code", out["error"])
            rig.voice._redirect_mode = False
            out = await rig.voice.press_menu(CODE)
            self.assertFalse(out["ok"])
            await rig.flush()
            self.assertEqual(fb.redirects, [])
            self.assertEqual(rig.client.audio_out(), sealed_audio)            # nothing else was played
            self.assertEqual(rig.voice.counters["dtmf_refused"], 5)
            # A Grok tool call with the code (the model never sees it, but if the other side read it out):
            await rig.state("agent_talking")
            await rig.voice.begin_conversation("x")
            await rig.flush()
            rig.hub.last.tool_call("press_keys", {"digits": CODE})
            await rig.flush()
            self.assertFalse(rig.hub.last.tool_results[-1]["output"]["ok"])
            self.assertEqual(fb.redirects, [])
            await rig.close()
        run(go())

    def test_plan_dies_if_a_code_is_released_before_twilio_fetches_it(self):
        clock = VirtualClock()
        fb = DtmfFallback(clock)
        nonce = fb.prepare("c1", CODE, origin="menu")
        fb.guard.add(CODE)
        self.assertIsNone(fb.take("c1", nonce))

    def test_only_menu_origin_and_valid_digits(self):
        fb = DtmfFallback(VirtualClock())
        for origin in ("sealed", "frame", "otp", ""):
            with self.assertRaises(DigitsRefused):
                fb.prepare("c1", "1", origin=origin)
        for bad in ("", "12,3", "1" * 33, "+1", "A"):
            with self.assertRaises(DigitsRefused):
                fb.prepare("c1", bad, origin="menu")

    def test_plans_are_single_use_bound_and_expire(self):
        clock = VirtualClock()
        fb = DtmfFallback(clock, ttl_s=60)
        n1 = fb.prepare("c1", "1", origin="menu")
        self.assertIsNone(fb.take("c2", n1))                 # wrong call burns it
        self.assertIsNone(fb.take("c1", n1))
        n2 = fb.prepare("c1", "2", origin="menu")
        clock.advance(61)
        self.assertIsNone(fb.take("c1", n2))
        n3 = fb.prepare("c1", "3", origin="menu")
        self.assertEqual(fb.take("c1", n3), "3")

    def test_guard_keeps_no_plaintext(self):
        g = SealedGuard()
        g.add(CODE)
        self.assertTrue(g.matches("9" + CODE))
        self.assertFalse(g.matches("482914"))
        self.assertNotIn(CODE, repr(vars(g)))
        self.assertNotIn(CODE.encode(), b"".join(m for _, m in g._codes))


class SignalAndTwimlTests(unittest.TestCase):
    def test_menu_reaction_signal(self):
        self.assertTrue(menu_did_not_react(PROMPT, PROMPT))
        self.assertTrue(menu_did_not_react(PROMPT, "For reservations press 1. For hours and directions press 2."))
        self.assertTrue(menu_did_not_react(PROMPT, "That is not a valid entry."))
        self.assertTrue(menu_did_not_react(PROMPT, "Sorry, I didn’t catch that."))
        self.assertFalse(menu_did_not_react(PROMPT, "Please hold while we transfer your call."))
        self.assertFalse(menu_did_not_react(PROMPT, "Reservations. Please say the date of your booking."))
        self.assertFalse(menu_did_not_react(PROMPT, ""))
        # Sub-menus that look like the main menu must not cause a second press:
        for submenu in ("For new reservations, press 1.", "For hours, press 1. For directions, press 2.",
                        "For new reservations, press 1. To change an existing reservation, press 2.",
                        "Main menu. For reservations, press 1.", "Press 1."):
            self.assertFalse(menu_did_not_react(PROMPT, submenu), submenu)

    def test_play_digits_twiml(self):
        doc = play_digits_then_stream(digits="1w2#", ws_url="wss://x/t/s/twilio/media/c/tok",
                                      action_url="https://x/a", call_id="c")
        self.assertTrue(doc.startswith('<Play digits="1w2#"/><Connect action="https://x/a" method="POST">'))
        for bad in ("1;2", "", "1" * 33, '1"/><Hangup/>'):
            with self.assertRaises(ValueError):
                play_digits_then_stream(digits=bad, ws_url="wss://x/s", action_url="https://x/a", call_id="c")
        with self.assertRaises(ValueError):
            play_digits_then_stream(digits="1", ws_url="wss://x/s?t=1", action_url="https://x/a", call_id="c")


if __name__ == "__main__":
    unittest.main()
