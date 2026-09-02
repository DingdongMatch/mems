"""System clock adapter."""

from __future__ import annotations

from datetime import datetime, timezone


class SystemClock:
    """Return real wall-clock UTC times."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


system_clock = SystemClock()


def get_clock() -> SystemClock:
    return system_clock
