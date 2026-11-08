"""Single-owner lease per profile and line.

The lock is an exclusive, non-blocking OS file lock (``fcntl.flock`` on POSIX,
``msvcrt.locking`` on Windows). The kernel drops it when the owning process
dies, so a crashed owner never blocks a successor. The lease row in the store
tells other processes who owns the line and whether it is ready; a process
that loses the race reports ``standby`` and never claims readiness.
"""
from __future__ import annotations

import os
import socket
import uuid
from pathlib import Path
from typing import Optional

try:
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore
    import msvcrt  # type: ignore


class LeaseHeld(Exception):
    def __init__(self, holder: Optional[dict]):
        super().__init__("line is owned by another process")
        self.holder = holder


class Lease:
    def __init__(self, lock_dir: Path, line_key: str, store) -> None:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in line_key) or "default"
        self.path = Path(lock_dir) / f"line-{safe}.lock"
        self.line_key = line_key
        self.store = store
        self.owner_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._fh = None
        self.previous: Optional[dict] = None

    @property
    def held(self) -> bool:
        return self._fh is not None

    def acquire(self) -> None:
        if self._fh is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+")
        try:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:  # pragma: no cover
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            fh.close()
            raise LeaseHeld(self.store.read_lease(self.line_key)) from None
        self._fh = fh
        # The row left by a previous owner tells recovery whether it shut down cleanly.
        self.previous = self.store.read_lease(self.line_key)
        self.store.write_lease(self.line_key, self.owner_id, os.getpid(), "starting")

    def mark(self, status: str, detail: str = "") -> None:
        if self._fh is not None:
            self.store.write_lease(self.line_key, self.owner_id, os.getpid(), status, detail)

    def release(self, status: str = "stopped") -> None:
        if self._fh is None:
            return
        try:
            self.mark(status)
        except Exception:
            pass
        try:
            if fcntl is not None:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            else:  # pragma: no cover
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            self._fh.close()
            self._fh = None

    def previous_crashed(self) -> bool:
        """True when the last owner never reached a clean ``stopped``."""
        return bool(self.previous and self.previous.get("status") not in ("stopped",)
                    and self.previous.get("owner_id") != self.owner_id)
