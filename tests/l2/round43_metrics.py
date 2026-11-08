"""Round 43 before/after detector numbers. Run with PYTHONPATH=plugin:tests/l2 python3 tests/l2/round43_metrics.py.

Every existing held-out/control corpus (false "person" and missed people), plus a new authored stress set: replies to
our live-person question that START with "yes"/"I am"/"this is" but come from a bot, a recording or a menu (round 43
broadens the clear yes to replies that start with one), and IVR "are you there" prompts. Offline, no model I/O."""
import json
import sys
from pathlib import Path
from line.detect import HoldTracker, Utterance as U

import evaluate
import test_airline_hold
import test_agent_greetings
import test_assistants
import test_delta_run2
import test_delta_run3
import test_r2_openers

BOT_PREFIX = ["Thanks for calling Hilton Hotels.", "I'm Hilton's virtual assistant.", "Are you calling about a reservation?"]
QUESTION = "Am I speaking with a live person?"
# Authored stress negatives (round 43): bots, recordings and menus that start like a yes.
YES_LIKE_NON_HUMAN = [
    "Yes, I'm a virtual assistant.", "Yes, I am Hilton's automated assistant. How can I help?",
    "I am a digital assistant.", "This is Ava, your AI assistant. How can I help?",
    "Yes. Please say or press one for reservations.", "Yes, to receive a callback, press one.",
    "Yes, I can help with billing. Please say your account number.", "Yes? Are you still there?", "Yes, no.",
    "I'm sorry, I didn't get that.", "Yes or no?", "Yes, this call may be recorded for quality purposes.",
    "Yes, please hold while I transfer you.", "Yeah, I'm not a live person.",
    "This is an automated message. Please hold.", "Yes, our representatives are busy. Please stay on the line.",
    "Yes, would you like to make a payment?", "Speaking? Is that correct?", "Yes, I am virtual.", "You are speaking with a virtual assistant. How can I help?",
    "Yes, you are speaking with an automated agent.", "You are not speaking with a live person.", "Yes, enjoy our special offer.",
    "Yes, please enter your confirmation number.", "Yes, I'm a recording.", "Yes, press two for Honors.",
]
# Queue recordings that start with "yes" (the engine accepts a clear yes to our question whatever L2's label).
YES_LIKE_QUEUE = ["Yes, please hold while I transfer you.", "Yes, your call is important to us.",
                  "Yes, all of our representatives are busy.", "Yes, the current wait time is 20 minutes.",
                  "Yes, thank you for waiting.", "Yes, I will transfer you now.", "Yes, please stay on the line."]
ARE_YOU_THERE = ["Are you still there?", "Hello? Are you there?", "Are you still there? Please say yes or no.",
                 "Sorry, I didn't hear you. Are you still there?", "Hello? Are you a live person too?"]
# Authored people answering our question (round 43 target; the first is the Hilton live reply).
YES_LIKE_HUMAN = ["Yes. Are you a live person too?", "Yes. Are you a robot?", "I'm a live person.", "Uh, yes.",
                  "I am. Who is this?", "Yes, this is Chris.", "Speaking. Who is calling?",
                  # Hilton call 2 (round 43b): real agents, verbatim
                  "Yes. You are speaking with the live person. Yes. Indeed.", "Yes. You are speaking with a live person.",
                  "You are speaking to a live person. How can I help you?",
                  "Yes. You are speaking with a live person. How can I help you? Okay.",
                  # Round 43c (STEER-4): any words may follow an explicit affirmation
                  "Yes, how can I help you?", "Yes, this is Chris. How can I help you?", "Yeah, I'm here.",
                  "Yes, I am a real person and I can help", "Yes. Okay."]


def hilton(verdict):
    """Every business line the agents said after our live-person question on the three real Hilton calls."""
    root = Path(__file__).resolve().parents[2] / "fixtures/hold-sim"
    rows = {}
    for name, start in (("call 1", "This is Chris."), ("call 2 retry", "Thank you for thank you for calling Hilton.")):
        lines = json.loads((root / f"hilton-run{1 if name == 'call 1' else 2}.json").read_text())["transcript"]
        i = next(n for n, t in enumerate(lines) if t["speaker"] == "business" and start in t["text"])
        replies = [t["text"] for n, t in enumerate(lines[i:], i) if t["speaker"] == "business"
                   and any(x["speaker"] == "hermes" for x in lines[i:n])]
        rows[name] = [(t, verdict(t)) for t in replies]
    menu = json.loads((root / "hilton-call2-menu.json").read_text())["transcript"]
    rows["call 2 attempt 1 (menu, no person)"] = [(t["text"], verdict(t["text"])) for t in menu if t["speaker"] == "business"]
    return rows


def after_question(text):
    t = HoldTracker()
    for i, line in enumerate(BOT_PREFIX):
        t.feed(U(line, i * 1000, turn_id=str(i)))
    t.feed(U(QUESTION, 5000, "hermes", turn_id="q"))
    return t.feed(U(text, 7000, turn_id="r"))


def main():
    out = {}
    for split in ("train", "held-out", "soak"):
        for model in (False, True):
            r = evaluate.evaluate(split, model)
            out[f"hold-sim {split} ({r['mode']})"] = dict(false_human=r["false_human"], negative_events=r["negative_events"],
                                                          missed_human_events=r["missed_human_events"])
    for split in ("train", "held-out"):
        for model in (False, True):
            r = test_airline_hold.score(split, model)
            out[f"airline {split} ({r['mode']})"] = dict(false_human=r["false_human"], missed_human=r["missed_human"])
    g = test_agent_greetings.replay()
    out["agent-greetings"] = dict(humans=g["humans"], bots=g["bots"], missed_humans=g["missed_humans"],
                                  false_humans=g["false_humans"])
    for model in (False, True):
        a = test_assistants.replay(model)
        out[f"assistants ({a['mode']})"] = dict(false_humans=a["false_humans"], missed_humans=a["missed_humans"])
    d2 = test_delta_run2.replay()
    out["delta-run2 replay"] = dict(false_humans=d2["false_humans"])
    for model in (False, True):
        _, d3 = test_delta_run3.tracker_replay(model)
        out[f"delta-run3 queue replay (model={model})"] = dict(unsafe=d3["unsafe"])
    o = test_r2_openers.scores()
    out["r2 openers"] = dict(missed_candidates=o["missed_candidates"])
    rows = [(x, after_question(x).label) for x in YES_LIKE_NON_HUMAN + YES_LIKE_QUEUE + ARE_YOU_THERE]
    out["round43 stress: yes-like non-human after our question"] = dict(
        cases=len(rows), false_human=sum(label == "human" for _, label in rows),
        which=[x for x, label in rows if label == "human"])
    try:
        from line.detect.affirmative import clear_yes_reply
    except ImportError:  # before round 43: the engine used clear_yes
        from line.detect.affirmative import clear_yes as clear_yes_reply
    engine = YES_LIKE_NON_HUMAN + YES_LIKE_QUEUE + ARE_YOU_THERE
    out["round43 stress: engine clear-yes test on the same non-human replies"] = dict(
        cases=len(engine), accepted=sum(map(clear_yes_reply, engine)), which=[x for x in engine if clear_yes_reply(x)])
    try:
        from line.detect.affirmative import reply_verdict
        out["round43c engine verdict on the non-human replies (unclear = re-ask, then the owner judges)"] = dict(
            yes=[x for x in engine if reply_verdict(x) == "yes"], unclear=[x for x in engine if reply_verdict(x) == "unclear"],
            bot=sum(reply_verdict(x) == "bot" for x in engine))
        out["round43c engine verdict on the human target set"] = dict(
            yes=sum(reply_verdict(x) == "yes" for x in YES_LIKE_HUMAN),
            not_yes=[(x, reply_verdict(x)) for x in YES_LIKE_HUMAN if reply_verdict(x) != "yes"])
        out["round43c engine verdict on the three Hilton exchanges (agent replies after our question)"] = hilton(reply_verdict)
    except ImportError:
        out["round43c engine verdicts"] = "not available before round 43c (the engine used clear_yes only)"
        out["round43c engine verdict on the three Hilton exchanges (agent replies after our question)"] = hilton(
            lambda t: "yes" if clear_yes_reply(t) else "not yes")
    rows = [(x, after_question(x).label) for x in YES_LIKE_HUMAN]
    out["round43 target: people answering our question"] = dict(
        cases=len(rows), human=sum(label == "human" for _, label in rows),
        missed=[x for x, label in rows if label != "human"])
    json.dump(out, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
