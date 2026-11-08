"""Pre-run guard for the live latency run (coordinator, L1 round 2).

Reads ``$HERMES_HOME/auth.json`` as data (never through Hermes, so nothing is
refreshed or rewritten) and checks that every anthropic credential in the pool,
plus the anthropic singleton if present, expires more than MIN_MINUTES from now.
Prints only source, auth type, whether a refresh token exists, minutes left and
last status. Never prints a token, a label or any other value. Exit 0 = go.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

MIN_MINUTES = 60


def _minutes_left(entry: dict) -> float | None:
    now = time.time()
    ms = entry.get("expires_at_ms")
    if isinstance(ms, (int, float)) and ms > 0:
        return (ms / 1000.0 - now) / 60.0
    raw = entry.get("expires_at")
    if isinstance(raw, (int, float)):
        return ((raw / 1000.0 if raw > 1e12 else raw) - now) / 60.0
    if isinstance(raw, str) and raw:
        try:
            return (datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp() - now) / 60.0
        except ValueError:
            return None
    return None


def main() -> int:
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    data = json.loads((home / "auth.json").read_text(encoding="utf-8"))
    rows = []
    pool = (data.get("credential_pool") or {}).get("anthropic") or []
    for i, entry in enumerate(pool if isinstance(pool, list) else []):
        rows.append({"where": f"pool[{i}]", "source": entry.get("source"), "auth_type": entry.get("auth_type"),
                     "has_refresh": bool(entry.get("refresh_token")), "minutes_left": _minutes_left(entry),
                     "last_status": entry.get("last_status")})
    single = (data.get("providers") or {}).get("anthropic")
    if isinstance(single, dict):
        rows.append({"where": "providers.anthropic", "source": single.get("source"),
                     "auth_type": single.get("auth_type"), "has_refresh": bool(single.get("refresh_token")),
                     "minutes_left": _minutes_left(single), "last_status": single.get("last_status")})
    ok = bool(rows)
    for row in rows:
        left = row["minutes_left"]
        is_oauth = str(row.get("auth_type") or "").lower() not in ("api_key", "")
        row["ok"] = (left is not None and left > MIN_MINUTES) if (is_oauth or left is not None) else True
        if row["minutes_left"] is not None:
            row["minutes_left"] = round(row["minutes_left"], 1)
        ok = ok and row["ok"]
    print(json.dumps({"min_minutes": MIN_MINUTES, "credentials": rows, "go": ok}, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
