"""Real-model latency through the owner's current Hermes model route.

Not a unit test and not offline: each turn is one real inference on the configured
route. No phone, no Twilio, no tunnel: the call service runs on loopback with the
fake adapter, and a fake ConversationRelay client plays a scripted SAMPLE business
(Sample Bistro). ``--driver embedded`` uses ``tests/l1/bench/embedded_factory.py``
(reasoning effort ``low``), restricted to the plugin's in-call toolset so the
measurement cannot touch any other tool. At most 20 turns.

Measured per turn, at the fake relay: prompt sent -> first non-whitespace text
token (``first_text_ms``) and -> the ``last: true`` marker (``full_ms``). The
driver's own first-delta time is recorded too. STT, TTS and telephone network
latency are NOT included.

Run (see docs/lanes/L1.md for the exact command and environment):
    HERMES_AGENT_ROOT=/Users/mac/.hermes/hermes-agent python -B tests/l1/bench/latency_real_model.py OUT.json
"""
from __future__ import annotations

import json
import math
import os
import platform
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))  # tests/l1 (harness)
sys.path.insert(0, str(HERE.parent))      # tests/l1/bench (embedded_factory)
sys.path.insert(0, os.environ.get("HERMES_AGENT_ROOT", "/Users/mac/.hermes/hermes-agent"))

from harness import Line, wait_for  # noqa: E402

MAX_TURNS = 20
SCRIPT = [  # what the SAMPLE business says, one line per turn
    "Yes, I am a real person",
    "Sure, what day and time were you thinking?",
    "And for how many people?",
    "Okay. What name should I put the reservation under?",
    "We're actually full at seven. Would seven thirty work instead?",
    "Great. Indoor or patio?",
    "Any allergies or a special occasion we should know about?",
    "Perfect. Can I get a phone number in case we need to reach you?",
    "Got it. Just so you know, we hold tables for fifteen minutes.",
    "Is there anything else I can help with?",
    "Do you need directions or parking information?",
    "Alright, you're all set for Friday at seven thirty for four.",
    "Sorry, could you repeat the name once more?",
    "Thanks. We'll see you Friday. Have a good one!",
]
GOAL = ("Book a table for four people this Friday at 7 pm at Sample Bistro under the name Sam. 7:30 pm is also "
        "fine. Indoor seating preferred. No allergies. Do not give out any phone number; say Sam will call if "
        "needed.")


def _route() -> dict:
    """Non-secret route facts from config.yaml (model, provider); credentials are never read here."""
    try:
        from hermes_cli.config import load_config
        model = (load_config() or {}).get("model") or {}
        return {"model": model.get("default"), "provider": model.get("provider"), "reasoning_effort": "low",
                "credential": "resolved by Hermes, never read by this script"}
    except Exception as exc:
        return {"error": type(exc).__name__}


def pct(values, q):
    values = sorted(values)
    return values[max(0, math.ceil(q * len(values)) - 1)]


def _children() -> list[str]:
    try:
        import psutil
        return sorted({c.name() for c in psutil.Process().children(recursive=True)})
    except Exception as exc:  # pragma: no cover
        return [f"unavailable: {type(exc).__name__}"]


def main(out_path: str, driver: str = "embedded", max_turns: int = 14) -> None:
    assert len(SCRIPT) <= MAX_TURNS and max_turns <= 14
    from plugin.line.engine.driver import HermesAgentDriver
    from embedded_factory import build_agent_factory
    from tools.registry import registry

    holder: dict = {}
    kwarg_paths: list[str] = []

    def in_call(method, *args):
        def handler(tool_args, **kw):
            sid = kw.get("session_id") or getattr(kw.get("parent_agent"), "session_id", "") or ""
            kwarg_paths.append("session_id" if kw.get("session_id") else ("parent_agent" if sid else "none"))
            svc = holder["line"].svc
            values = {"press_digits": (str(tool_args.get("digits", "")),),
                      "request_finish": (str(tool_args.get("outcome", "")), str(tool_args.get("summary", ""))),
                      "request_owner": ()}[method]
            return json.dumps(svc.in_call(sid, method, *values))
        return handler

    from plugin.line.engine.hermes_glue import TOOLS
    for name, method in (("hermes_line_call_press_digits", "press_digits"),
                         ("hermes_line_call_finish", "request_finish"),
                         ("hermes_line_call_bring_owner", "request_owner")):
        registry.register(name=name, toolset="hermes_line", schema={"name": name, **TOOLS[name]},
                          handler=in_call(method))

    built: list[float] = []
    acp_info: dict = {}
    drivers: list = []
    goal = GOAL if driver == "embedded" else GOAL + " Do not use any tools during this call."
    if driver == "embedded":
        factory = build_agent_factory(reasoning_effort="low", max_iterations=4,
                                      extra={"enabled_toolsets": ["hermes_line"]})

        def timed_factory(briefing, session_id):
            t0 = time.perf_counter()
            try:
                agent = factory(briefing, session_id)
            except BaseException as exc:
                print(f"agent construction failed: {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
                raise
            built.append((time.perf_counter() - t0) * 1000)
            return agent

        make_driver = lambda sid: HermesAgentDriver(timed_factory, sid)  # noqa: E731
    else:
        from plugin.line.engine.acp_driver import AcpDriver

        class TimedAcp(AcpDriver):
            def start(self, briefing):
                t0 = time.perf_counter()
                super().start(briefing)
                built.append((time.perf_counter() - t0) * 1000)
                acp_info["session_id"] = self.hermes_session_id
                title = os.environ.get("L1_ACP_TITLE")
                if title:  # ACP's /title route is answered by the gateway, not the model
                    res = self._prompt(self.hermes_session_id, f"/title {title}", None, 30)
                    acp_info["title"] = {"sent": title, "reply": res.text.strip()[:200], "error": res.error}

            def ask(self, prompt, timeout_s=20.0):
                return ""  # bench: never open a second ACP session (detector and summary questions)

        def make_driver(sid):
            drv = TimedAcp(sid, command=json.loads(os.environ["L1_ACP_COMMAND"]), env=None, cwd=os.getcwd())
            drivers.append(drv)
            return drv

    line = Line(driver_factory=make_driver, turn_timeout_s=90.0, ask_timeout_s=30.0, max_call_minutes=30)
    holder["line"] = line
    results = []
    try:
        req, created = line.dial(goal=goal, title="Table for 4, Friday 7 pm")
        relay = line.connect(req, created)
        relay.prompt("Good evening, Sample Bistro, this is Mike speaking.")
        relay.wait_text("live person?", timeout=10)
        wait_for(lambda: built, timeout=60, what="Hermes session construction")
        children = []
        for i, line_text in enumerate(SCRIPT[:max_turns]):
            if line.store.get_call(req["call_id"]).state.terminal:
                break
            before_frames = len(relay.frames)
            t0 = time.perf_counter()
            relay.prompt(line_text)
            first = None
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                frames = relay.frames[before_frames:]
                if first is None and any(f.get("type") == "text" and (f.get("token") or "").strip() for f in frames):
                    first = (time.perf_counter() - t0) * 1000
                if any(f.get("type") == "text" and f.get("last") and not (f.get("token") or "").strip()
                       for f in frames) or any(f.get("type") == "end" for f in frames):
                    break
                time.sleep(0.002)
            full = (time.perf_counter() - t0) * 1000
            spoken = "".join(f.get("token", "") for f in relay.frames[before_frames:] if f.get("type") == "text")
            if i == 0:
                children = _children()
            usage = getattr(drivers[0], "last_usage", None) if drivers else None
            results.append({"usage": usage, "turn": i + 1, "kind": "cold" if i == 0 else "warm", "first_text_ms": round(first, 1)
                            if first else None, "full_ms": round(full, 1), "chars": len(spoken)})
        time.sleep(1.0)
        turn_events = [e["data"] for e in line.store.events(req["call_id"], kind="turn")]
        state = line.store.get_call(req["call_id"]).state.value
        transcript = line.store.transcript(req["call_id"])
    finally:
        line.close()

    warm = [r for r in results if r["kind"] == "warm" and r["first_text_ms"]]
    summary = {
        "what": "Hermes model route from HERMES_HOME config, fake ConversationRelay over loopback ingress; "
                "no STT/TTS/phone",
        "driver": driver,
        "acp": acp_info,
        "route": _route(),
        "safe_mode_env": os.environ.get("HERMES_SAFE_MODE", ""),
        "child_processes_after_first_turn": children,
        "machine": platform.machine(), "os": platform.mac_ver()[0], "python": platform.python_version(),
        "agent_construction_ms": [round(b, 1) for b in built],
        "turns": results,
        "driver_turn_events": turn_events,
        "in_call_tool_context": sorted(set(kwarg_paths)),
        "final_state": state,
        "transcript": transcript,
    }
    if warm:
        firsts = [r["first_text_ms"] for r in warm]
        fulls = [r["full_ms"] for r in warm]
        def stats(v):
            return {"p50": round(statistics.median(v), 1), "p90": pct(v, .90), "min": min(v), "max": max(v)}
        summary["warm_stats"] = {"n": len(warm), "first_text_ms": stats(firsts), "full_ms": stats(fulls)}
    Path(out_path).write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps({k: summary[k] for k in ("agent_construction_ms", "turns", "warm_stats", "final_state",
                                               "in_call_tool_context") if k in summary}, indent=1))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--driver", choices=["embedded", "acp"], default="embedded")
    ap.add_argument("--max-turns", type=int, default=14)
    a = ap.parse_args()
    main(a.out, a.driver, a.max_turns)
