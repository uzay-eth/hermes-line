"""Hermes call driver: one dedicated, serialized Hermes session per call.

Interface (ADR 0002 item 4): ``start(briefing)``, ``turn(text, emit)``,
``interrupt(spoken_prefix)``, ``close()``, plus ``ask(prompt, timeout_s)`` for
stateless auxiliary questions (line.detect's injected model) and ``note`` to
record context such as owner "Say this" lines.

Barge-in: every turn has a generation number. ``interrupt`` bumps it, so any
chunk the model still produces for the old generation is dropped, asks the
Hermes agent to stop, and rewrites the last assistant message in the retained
history to the text the caller actually heard. The next turn therefore never
assumes the caller heard an unsaid sentence.

``turn``, ``ask`` and ``note`` block and must run on the call's worker thread
(``engine.machine`` does this); ``interrupt`` is thread-safe.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

INTERRUPTED_SUFFIX = " [interrupted: the rest was not heard]"


@dataclass
class TurnResult:
    text: str = ""
    interrupted: bool = False
    error: Optional[str] = None
    first_text_ms: Optional[float] = None
    full_ms: float = 0.0
    tool_calls: list = field(default_factory=list)


class CallDriver(Protocol):
    session_id: str

    def start(self, briefing: str) -> None: ...
    def turn(self, text: str, emit: Callable[[str], None]) -> TurnResult: ...
    def interrupt(self, spoken_prefix: Optional[str] = None) -> None: ...
    def ask(self, prompt: str, timeout_s: float = 20.0) -> str: ...
    def note(self, text: str) -> None: ...
    def close(self) -> None: ...


def reconcile_history(messages: list, spoken: Optional[str]) -> list:
    """Replace the last assistant text with what was actually spoken."""
    out = [dict(m) if isinstance(m, dict) else m for m in messages]
    for msg in reversed(out):
        if isinstance(msg, dict) and msg.get("role") == "assistant" and isinstance(msg.get("content"), str) \
                and msg.get("content"):
            heard = (spoken or "").strip()
            msg["content"] = (heard + INTERRUPTED_SUFFIX) if heard else INTERRUPTED_SUFFIX.strip()
            break
    return out


class HermesAgentDriver:
    """Drives an in-process agent built by ``agent_factory(briefing, session_id)``.

    Not used by the shipped plugin (which runs calls over ACP, ``acp_driver``); kept as the
    reference implementation of the driver interface for hosts that embed an agent. Works on
    any object with
    ``run_conversation(text, conversation_history=..., stream_callback=...)``,
    ``interrupt()`` and ``close()``.
    """

    def __init__(self, agent_factory: Callable[[str, str], Any], session_id: str):
        self.session_id = session_id
        self._factory = agent_factory
        self._agent: Any = None
        self._history: list = []
        self._lock = threading.Lock()
        self._gen = 0
        self._running_gen: Optional[int] = None
        self._interrupted: set[int] = set()
        self._spoken: dict[int, Optional[str]] = {}

    def start(self, briefing: str) -> None:
        if self._agent is None:
            self._agent = self._factory(briefing, self.session_id)

    def turn(self, text: str, emit: Callable[[str], None]) -> TurnResult:
        if self._agent is None:
            raise RuntimeError("driver not started")
        with self._lock:
            self._gen += 1
            gen = self._gen
            self._running_gen = gen
        started = time.perf_counter()
        first: list[float] = []
        spoken_chunks: list[str] = []

        def on_delta(delta: Any) -> None:
            if not isinstance(delta, str) or not delta:
                return
            with self._lock:
                if gen != self._gen or gen in self._interrupted:
                    return  # stale generation: never reaches the caller
            if not first and delta.strip():
                first.append((time.perf_counter() - started) * 1000)
            spoken_chunks.append(delta)
            emit(delta)

        error = None
        try:
            result = self._agent.run_conversation(text, conversation_history=list(self._history),
                                                  stream_callback=on_delta) or {}
        except Exception as exc:  # the call must survive a failed turn
            result = {"messages": self._history + [{"role": "user", "content": text}],
                      "final_response": "", "error": type(exc).__name__}
        messages = result.get("messages") or (self._history + [{"role": "user", "content": text}])
        final = result.get("final_response") or ""
        error = result.get("error") or None
        with self._lock:
            interrupted = gen in self._interrupted
            spoken = self._spoken.get(gen)
            self._running_gen = None
        if not spoken_chunks and final and not interrupted:
            # Non-streaming provider: deliver the whole answer once.
            first.append((time.perf_counter() - started) * 1000)
            spoken_chunks.append(final)
            emit(final)
        if interrupted:
            messages = reconcile_history(messages, spoken if spoken is not None else "".join(spoken_chunks))
        self._history = list(messages)
        return TurnResult(text="".join(spoken_chunks), interrupted=interrupted, error=error,
                          first_text_ms=first[0] if first else None,
                          full_ms=(time.perf_counter() - started) * 1000)

    def interrupt(self, spoken_prefix: Optional[str] = None) -> None:
        with self._lock:
            running = self._running_gen
            target = running if running is not None else self._gen
            self._interrupted.add(target)
            self._spoken[target] = spoken_prefix
            self._gen += 1  # any later chunk for ``target`` is stale
        if running is not None:
            try:
                self._agent.interrupt()
            except Exception:
                pass
        else:
            # The turn already finished but text-to-speech was cut short.
            self._history = reconcile_history(self._history, spoken_prefix)

    def ask(self, prompt: str, timeout_s: float = 20.0) -> str:
        """Stateless question on the call's own Hermes session (history untouched)."""
        if self._agent is None:
            return ""
        out: list[str] = []
        done = threading.Event()

        def run() -> None:
            try:
                res = self._agent.run_conversation(prompt, conversation_history=[]) or {}
                out.append(str(res.get("final_response") or ""))
            except Exception:
                out.append("")
            finally:
                done.set()

        worker = threading.Thread(target=run, name=f"hermes-line-ask-{self.session_id}", daemon=True)
        worker.start()
        if not done.wait(timeout_s):
            try:
                self._agent.interrupt()
            except Exception:
                pass
            done.wait(5)
            return ""
        return out[0] if out else ""

    def note(self, text: str) -> None:
        self._history = list(self._history) + [{"role": "user", "content": f"[call note] {text}"},
                                               {"role": "assistant", "content": "Noted."}]

    @property
    def history(self) -> list:
        return list(self._history)

    def close(self) -> None:
        agent, self._agent = self._agent, None
        if agent is not None:
            try:
                agent.close()
            except Exception:
                pass
