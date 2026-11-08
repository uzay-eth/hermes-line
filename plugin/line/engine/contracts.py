"""Shared value types for the call engine.

The policy (L2) and SMS (L4) lanes define their own types for the contract
interfaces; the engine only relies on the attribute names the contract fixes
(``DestinationDecision.allowed/e164/reason``, ``Detection.label/confidence/
evidence``). The types here are the engine's own vocabulary.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Optional


class CallState(str, enum.Enum):
    """Call states exactly as docs/CONTRACT.md names them."""

    AWAITING_APPROVAL = "awaiting_approval"
    DIALING = "dialing"
    IVR = "ivr"
    ON_HOLD = "on_hold"
    HUMAN_DETECTED = "human_detected"
    RINGING_OWNER = "ringing_owner"
    OWNER_CONNECTED = "owner_connected"
    AGENT_TALKING = "agent_talking"
    ENDED = "ended"
    FAILED = "failed"

    @property
    def terminal(self) -> bool:
        return self in (CallState.ENDED, CallState.FAILED)


TERMINAL_STATES = frozenset({CallState.ENDED, CallState.FAILED})
LIVE_STATES = frozenset(set(CallState) - TERMINAL_STATES - {CallState.AWAITING_APPROVAL})


class EndReason(str, enum.Enum):
    COMPLETED = "completed"            # task finished on the call
    OWNER_FINISHED = "owner_finished"  # owner took over and the bridge ended
    HUNG_UP_BY_OWNER = "hung_up_by_owner"
    VOICEMAIL = "voicemail"
    NO_ANSWER = "no_answer"
    BUSY = "busy"
    BUSINESS_HANGUP = "business_hangup"
    DROPPED = "dropped"
    OWNER_UNAVAILABLE = "owner_unavailable"
    DENIED = "denied"                  # owner refused approval
    EXPIRED = "expired"                # approval never arrived
    REJECTED = "rejected"              # a safety gate refused the dial
    PROVIDER_ERROR = "provider_error"
    MAX_DURATION = "max_duration"
    SERVICE_RESTART = "service_restart"
    CANCELED = "canceled"


class TaskStatus(str, enum.Enum):
    AWAITING_APPROVAL = "awaiting_approval"
    CALLING = "calling"
    AWAITING_CALLBACK = "awaiting_callback"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"

    @property
    def open(self) -> bool:
        return self in (TaskStatus.AWAITING_APPROVAL, TaskStatus.CALLING, TaskStatus.AWAITING_CALLBACK)


class Mode(str, enum.Enum):
    AGENT = "agent"   # Hermes completes the task itself
    OWNER = "owner"   # Hermes bridges the owner in when a human answers


class Speaker(str, enum.Enum):
    HERMES = "hermes"
    BUSINESS = "business"
    OWNER = "owner"
    SYSTEM = "system"


class Direction(str, enum.Enum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"


class Role(str, enum.Enum):
    """Why a leg is dialled. Every role passes the destination gate."""

    BUSINESS = "business"
    OWNER = "owner"


@dataclass
class Origin:
    """Where a task was requested, captured at creation (never inferred later)."""

    profile: str = ""
    session_id: str = ""
    session_key: str = ""
    platform: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"profile": self.profile, "session_id": self.session_id,
                "session_key": self.session_key, "platform": self.platform}

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> "Origin":
        data = data or {}
        return cls(**{k: str(data.get(k) or "") for k in ("profile", "session_id", "session_key", "platform")})


@dataclass
class TaskRecord:
    id: str
    title: str
    goal: str
    destination_e164: str
    destination_masked: str
    mode: Mode
    status: TaskStatus
    origin: Origin = field(default_factory=Origin)
    created_at: float = 0.0
    updated_at: float = 0.0
    outcome: str = ""
    summary: str = ""
    allow_agent_fallback: bool = True
    leave_voicemail: bool = False


@dataclass
class CallRecord:
    id: str
    task_id: Optional[str]
    direction: Direction
    counterpart_e164: str
    counterpart_masked: str
    state: CallState
    mode: Mode
    created_at: float
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    provider_sid: str = ""
    owner_sid: str = ""
    conference: str = ""
    end_reason: str = ""
    summary: Optional[str] = None
    revision: int = 0
    control_revision: int = 0
    line_key: str = "default"
    state_since: Optional[float] = None
    transport: str = "conversation_relay"   # or "media_streams" (L5 voice path)
    phases: list = field(default_factory=list)  # round 37 (B9): [{"phase": str, "since": float}], oldest first


# Round 37 (B9): the phase each stored state opens unless the engine names a finer one ("assistant" for an automated
# assistant's IVR state, "fallback" once the owner did not take the call). None: no phase (not yet a call).
PHASES = ("dialing", "menu", "assistant", "hold", "person", "owner_ringing", "bridged", "fallback", "ended")
STATE_PHASE = {
    CallState.AWAITING_APPROVAL: None, CallState.DIALING: "dialing", CallState.IVR: "menu",
    CallState.ON_HOLD: "hold", CallState.HUMAN_DETECTED: "person", CallState.RINGING_OWNER: "owner_ringing",
    CallState.OWNER_CONNECTED: "bridged", CallState.AGENT_TALKING: "person", CallState.ENDED: "ended",
    CallState.FAILED: "ended",
}


class GateRejected(Exception):
    """A safety gate refused an action before any adapter call."""

    def __init__(self, gate: str, reason: str):
        super().__init__(f"{gate}: {reason}")
        self.gate = gate
        self.reason = reason
