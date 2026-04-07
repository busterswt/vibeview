"""Shared time-formatting helpers."""
from __future__ import annotations

from datetime import datetime, timezone


def format_uptime(since) -> str:
    """Return a human-readable age string from a timezone-aware datetime."""
    now = datetime.now(timezone.utc)
    delta = now - since
    total = int(delta.total_seconds())
    days = total // 86400
    hours = (total % 86400) // 3600
    mins = (total % 3600) // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m"
