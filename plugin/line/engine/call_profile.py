"""What the call session would load from its Hermes profile (Nous review of d92bc1fb, blocker 1).

What a caller or a business says goes into the call's Hermes session, which runs in ``call_profile``. A profile made
with ``hermes profile create --clone`` copies the owner's SOUL.md, MEMORY.md, USER.md, memory settings and the rest
of the owner's config.yaml, and Hermes loads them into that session.

Two layers keep them out:
1. The plugin starts every call session with Hermes's own ``--ignore-rules`` (``HERMES_IGNORE_RULES=1`` for the
   ``hermes acp`` child, ``acp_driver.AcpDriver(ignore_rules=True)``): no SOUL.md, no AGENTS.md/.cursorrules, no
   memory, no preloaded skills.
2. ``problems`` reads the profile before every call (at approval, at inbound admission and when the driver starts)
   and refuses it if anything below is set. A refused profile dials nobody and admits no inbound caller.

It is a finite list, not a check of everything Hermes loads: it reads the profile's ``config.yaml`` (not a managed
overlay), the names assigned in its ``.env`` (not their values), and whether ``hooks/``, ``plugins/`` and the memory
files have entries. The memory files are only stat'ed, never opened; only the problem strings leave this module. The
problem strings are exact and come in the order of the checks below.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .acp_driver import INHERITED_LAUNCH_FLAGS, PROMPT_ENV

MEMORY_FILES = ("memories/MEMORY.md", "memories/USER.md")
_OFF_WORDS = frozenset({"false", "no", "off", "0"})
# Hermes's neutral personality names (``hermes_cli.personality.NEUTRAL_PERSONALITY_NAMES``).
NEUTRAL_PERSONALITY = frozenset({"", "none", "default", "neutral"})
# config.yaml keys (exact dotted paths) that add text or context to every session, or start processes for it:
# the ephemeral system prompt (``display.personality`` naming a personality from ``personalities`` /
# ``agent.personalities``, else ``agent.system_prompt``), few-shot prefill (``prefill_messages_file``, legacy
# ``agent.prefill_messages_file``), the environment hint, the coding posture's "Operator instructions (from config)"
# (``agent.coding_instructions``, added even with --ignore-rules: S7 delta review R2), per-platform prompt hints,
# shell hooks (a ``pre_llm_call`` hook adds per-turn context) and MCP servers (each configured server is started for
# the session).
SET_KEYS = (("display", "personality"), ("agent", "system_prompt"), ("personalities",), ("agent", "personalities"),
            ("prefill_messages_file",), ("agent", "prefill_messages_file"), ("agent", "environment_hint"),
            ("agent", "coding_instructions"), ("platform_hints",), ("hooks",), ("mcp_servers",))
# Names the profile's ``.env`` may not assign, whatever the value: Hermes loads that file over the environment the
# plugin gives the child (``override=True``), so ``HERMES_IGNORE_RULES=0`` there would undo --ignore-rules, and the
# prompt names would add text to every session (S7 delta review R2).
DOTENV_REFUSED = INHERITED_LAUNCH_FLAGS + PROMPT_ENV
_DOTENV_NAME = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_.]*)\s*=")


def _off(value: Any) -> bool:
    """Hermes treats a missing memory flag as on; only an explicit false counts as off."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value == 0
    return isinstance(value, str) and value.strip().lower() in _OFF_WORDS


def _get(config: Any, path: tuple) -> Any:
    for key in path:
        if not isinstance(config, dict):
            return None
        config = config.get(key)
    return config


def is_empty(value: Any, path: tuple = ()) -> bool:
    """Empty, by type: None; False; a blank string (for ``display.personality``: a neutral name); an empty mapping
    or list. Anything else (other strings, numbers, True, non-empty mappings or lists) is set."""
    if value is None or value is False:
        return True
    if isinstance(value, str):
        text = value.strip()
        return text.lower() in NEUTRAL_PERSONALITY if path == ("display", "personality") else not text
    if isinstance(value, (dict, list, tuple)):
        return not value
    return False


def _norm(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").strip().split("\n"))


def _has_entries(directory: Path) -> bool:
    try:
        return any(not entry.name.startswith((".", "__")) for entry in directory.iterdir())
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _has_subdirs(directory: Path) -> bool:
    """Hermes loads every directory under the profile's ``hooks/`` (hidden ones too); files there are skipped."""
    try:
        return any(entry.is_dir() for entry in directory.iterdir())
    except FileNotFoundError:
        return False
    except OSError:
        return True


def dotenv_names(path: Path) -> Optional[set]:
    """The names a dotenv file assigns (``NAME=``, ``export NAME=``), or None when it cannot be read; values are
    never kept. A quoted multi-line value whose lines look like assignments adds names too (it can only refuse more)."""
    try:
        text = Path(path).read_bytes().decode("utf-8-sig", errors="replace")
    except FileNotFoundError:
        return set()
    except OSError:
        return None
    return {m.group(1) for m in map(_DOTENV_NAME.match, text.splitlines()) if m}


def problems(profile_dir: Path, neutral_souls: Iterable[str] = (),
             is_stock_soul: Optional[Callable[[str], bool]] = None) -> list[str]:
    """[] when the profile is neutral, else the exact problem strings, in this order:
    ``profile not found`` or ``config.yaml unreadable`` (alone); ``memory.memory_enabled not false``,
    ``memory.user_profile_enabled not false``, ``memory.provider set``, ``<dotted key> set`` for each ``SET_KEYS``
    entry, ``plugins.enabled not empty`` or ``plugins installed while plugins.enabled is unset``,
    ``.env sets <NAME>`` for each ``DOTENV_REFUSED`` name it assigns (or ``.env unreadable``), ``hooks/ not empty``,
    ``memories/MEMORY.md not empty``, ``memories/USER.md not empty``, ``SOUL.md not stock or empty`` (or the
    ``unreadable`` form of the last three). A neutral SOUL.md is missing, blank, one of ``neutral_souls`` (Hermes's
    stock text) or one ``is_stock_soul`` accepts (Hermes's earlier auto-seeded templates)."""
    profile_dir = Path(profile_dir)
    if not profile_dir.is_dir():
        return ["profile not found"]
    try:
        import yaml
        config = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        config = {}
    except Exception:
        return ["config.yaml unreadable"]
    if not isinstance(config, dict):
        return ["config.yaml unreadable"]
    found = [f"memory.{key} not false" for key in ("memory_enabled", "user_profile_enabled")
             if not _off(_get(config, ("memory", key)))]
    if not is_empty(_get(config, ("memory", "provider"))):
        found.append("memory.provider set")
    found += [".".join(path) + " set" for path in SET_KEYS if not is_empty(_get(config, path), path)]
    enabled = _get(config, ("plugins", "enabled"))
    if isinstance(enabled, list):
        if enabled:
            found.append("plugins.enabled not empty")
    elif _has_entries(profile_dir / "plugins"):  # unset: Hermes may grandfather installed plugins into the list
        found.append("plugins installed while plugins.enabled is unset")
    names = dotenv_names(profile_dir / ".env")
    if names is None:
        found.append(".env unreadable")
    else:
        found += [f".env sets {name}" for name in DOTENV_REFUSED if name in names]
    if _has_subdirs(profile_dir / "hooks"):  # gateway event hooks: Python Hermes loads from the profile's hooks/
        found.append("hooks/ not empty")
    for rel in MEMORY_FILES:
        try:
            if (profile_dir / rel).stat().st_size:
                found.append(f"{rel} not empty")
        except FileNotFoundError:
            pass
        except OSError:
            found.append(f"{rel} unreadable")
    try:
        soul = (profile_dir / "SOUL.md").read_text(encoding="utf-8")
    except FileNotFoundError:
        soul = ""
    except Exception:
        return found + ["SOUL.md unreadable"]
    stock = {_norm(text) for text in neutral_souls}
    if _norm(soul) and _norm(soul) not in stock and not (is_stock_soul is not None and is_stock_soul(soul)):
        found.append("SOUL.md not stock or empty")
    return found


def refusal(name: str, found: list[str]) -> str:
    """What the owner reads (approval reply, start-up notice, event log): each problem, then the fix."""
    shown = name or "<call_profile>"
    return (f"call profile not neutral: {'; '.join(found)}. Fix: delete and recreate the call profile with "
            f"`hermes profile delete {shown}`, `hermes profile create {shown}` (no --clone), "
            f"`hermes --profile {shown} model` (its model, provider and key), then the config lines in the README, "
            f"setup step 4")
