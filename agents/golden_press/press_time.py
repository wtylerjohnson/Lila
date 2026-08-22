"""One deterministic local press date for every rendered and validated surface."""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_PRESS_TIMEZONE = "America/Denver"


def local_press_date(value: object) -> str:
    """Return the saved invocation timestamp's date in the press timezone."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10] if len(text) >= 10 else ""
    if parsed.tzinfo is None:
        return parsed.date().isoformat()
    name = os.environ.get("LILA_PRESS_TIMEZONE", DEFAULT_PRESS_TIMEZONE)
    try:
        zone = ZoneInfo(name)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo(DEFAULT_PRESS_TIMEZONE)
    return parsed.astimezone(zone).date().isoformat()
