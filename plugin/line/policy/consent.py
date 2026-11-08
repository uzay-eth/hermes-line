"""Process-local single-use capability store. Restart revokes all approvals."""
import hashlib
import json
import math
import secrets
import threading
import time


def _binding(action, task_id, binding):
    if not isinstance(action, str) or not action or not isinstance(task_id, str) or not task_id or not isinstance(binding, dict):
        raise ValueError('action, task and binding are required')
    # JSON snapshot prevents caller mutation from changing approval scope.
    value = json.dumps([action, task_id, binding], sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(value) > 16384:
        raise ValueError('binding too large')
    return hashlib.sha256(value.encode()).digest()


class ConsentStore:
    def __init__(self, clock=time.monotonic, capacity=1024):
        self._clock, self._capacity = clock, capacity
        self._entries = {}
        self._lock = threading.Lock()

    def issue(self, action, task_id, binding: dict, ttl_s):
        if type(ttl_s) not in (int, float) or not math.isfinite(ttl_s) or not 0 < ttl_s <= 3600:
            raise ValueError('ttl_s must be positive and at most 3600')
        digest = _binding(action, task_id, binding)
        with self._lock:
            now = self._clock()
            self._entries = {k: v for k, v in self._entries.items() if v[0] > now}
            if len(self._entries) >= self._capacity:
                raise ValueError('approval store full')
            token = secrets.token_urlsafe(32)
            self._entries[hashlib.sha256(token.encode()).digest()] = (now + ttl_s, digest)
            return token

    def consume(self, token, action, task_id, binding) -> bool:
        if not isinstance(token, str) or len(token) > 128:
            return False
        with self._lock:
            entry = self._entries.pop(hashlib.sha256(token.encode()).digest(), None)
            if entry is None or entry[0] <= self._clock():
                return False
            try:
                digest = _binding(action, task_id, binding)
            except (ValueError, TypeError):
                return False
            return secrets.compare_digest(entry[1], digest)
