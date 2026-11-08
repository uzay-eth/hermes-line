"""SecretText: a string that refuses to print itself.

Every SMS body and every extracted code lives in one of these. repr/str/format all
return a fixed placeholder, pickling and JSON encoding fail, so an accidental
`log.info("got %s", msg)` or `f"{msg}"` cannot leak. The only way to the raw value is
`.reveal()`, and `reveal()` calls are the audit surface: grep for them.
"""
from __future__ import annotations

import hmac

_PLACEHOLDER = "<redacted>"


class SecretText:
    __slots__ = ("_v",)

    def __init__(self, value: str) -> None:
        if isinstance(value, SecretText):
            value = value._v
        if not isinstance(value, str):
            raise TypeError("SecretText wraps str only")
        object.__setattr__(self, "_v", value)

    def __setattr__(self, name, value):  # immutable
        raise AttributeError("SecretText is immutable")

    def reveal(self) -> str:
        """Return the raw value. Call only where the owner-approved result is built."""
        return self._v

    # Anything that formats the object gets the placeholder.
    def __repr__(self) -> str:
        return "SecretText(%s)" % _PLACEHOLDER

    def __str__(self) -> str:
        return _PLACEHOLDER

    def __format__(self, spec: str) -> str:
        return _PLACEHOLDER

    def __len__(self) -> int:
        return len(self._v)

    def __bool__(self) -> bool:
        return bool(self._v)

    def __eq__(self, other) -> bool:
        if isinstance(other, SecretText):
            return hmac.compare_digest(self._v.encode("utf-8"), other._v.encode("utf-8"))
        return NotImplemented

    def __hash__(self) -> int:
        return hash(("SecretText", self._v))

    # No serialisation paths.
    def __reduce__(self):
        raise TypeError("SecretText cannot be pickled")

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self
