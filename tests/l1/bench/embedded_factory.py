"""Embedded driver factory (benchmark only; NOT shipped since L1 round 3).

The documented Python embed (``run_agent.AIAgent``) in-process. It needs ``run_agent`` and
``hermes_cli.runtime_provider``, which the catalog's private-import lint flags, so the plugin
ships the ACP driver instead (live latency: docs/lanes/L1.md section 8). Kept here so the
embedded measurement stays reproducible with ``HermesAgentDriver``.
"""
from __future__ import annotations

from typing import Any, Callable, Optional


def build_agent_factory(*, reasoning_effort: Optional[str] = "low", skip_memory: bool = False,
                        max_iterations: int = 6, extra: Optional[dict] = None) -> Callable[[str, str], Any]:
    """Build ``AIAgent`` instances on the profile's configured model route.

    This is the documented Python embed (``run_agent.AIAgent``) with the route
    resolved exactly as the gateway and cron resolve it. Both imports are
    flagged by the catalog's private-import lint (ADR 0002 gate); they are kept
    here, lazily, so the rest of the plugin has no core coupling. The call
    session is not persisted to the Hermes session DB, so raw call text stays
    out of saved history; the plugin store keeps only the masked transcript.
    """

    def factory(briefing: str, session_id: str) -> Any:
        from hermes_cli.config import load_config  # public plugin surface
        from hermes_cli.runtime_provider import resolve_runtime_provider  # private: ADR 0002 gate
        from run_agent import AIAgent  # documented embed; private-import lint warning

        cfg = load_config() or {}
        model_cfg = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
        model = str(model_cfg.get("default") or model_cfg.get("model") or "")
        provider = model_cfg.get("provider") or None
        runtime = resolve_runtime_provider(requested=provider, target_model=model or None)
        reasoning = None
        if reasoning_effort:
            from hermes_constants import parse_reasoning_effort  # public plugin surface
            reasoning = parse_reasoning_effort(reasoning_effort)
        kwargs = dict(model=model, provider=runtime.get("provider"), base_url=runtime.get("base_url"),
                      api_key=runtime.get("api_key"), api_mode=runtime.get("api_mode"),
                      quiet_mode=True, ephemeral_system_prompt=briefing, session_id=session_id,
                      skip_context_files=True, skip_memory=skip_memory, skip_background_review=True,
                      max_iterations=max_iterations, reasoning_config=reasoning, platform="hermes-line")
        kwargs.update(extra or {})
        return AIAgent(**kwargs)

    return factory
