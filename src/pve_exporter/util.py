"""
Helpers shared by the exporter modules.
"""

import typing

FALSE_VALUES = ('0', 'false', 'no', 'off')

# How long the state kept for a target (caches, metric series) outlives its
# last scrape.
IDLE_SECONDS = 86400


def parse_bool(value, default: bool = False) -> bool:
    """
    Parse a boolean as PVE, URL parameters and environment variables write
    it (0/1, true/false, yes/no, on/off). Missing or blank is default.
    """
    if isinstance(value, (bool, int, float)):
        return bool(value)
    if value is None or not str(value).strip():
        return default
    return str(value).strip().lower() not in FALSE_VALUES


class IdleExpiry:  # pylint: disable=too-few-public-methods
    """
    The time each key (e.g. a target) was last used, to forget the state of
    keys that are not used any more. Not thread safe: callers hold their lock.
    """

    def __init__(self, idle_seconds: float = IDLE_SECONDS):
        self._idle_seconds = idle_seconds
        self._used = {}

    def use(self, key: typing.Hashable, now: float) -> list:
        """Record that key is used at now; return the keys idle for too long."""
        self._used[key] = now
        expired = [k for k, used in self._used.items() if now - used > self._idle_seconds]
        for k in expired:
            del self._used[k]
        return expired
