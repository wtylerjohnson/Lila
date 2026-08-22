"""Deterministic actionability classes for SAM.gov notice records."""

from __future__ import annotations

import re
from typing import Optional


_SPECIAL_ACTIONABLE_TITLE = re.compile(
    r"\b(?:RFI|request for information|draft\s+(?:RFP|solicitation)|"
    r"industry day)\b",
    re.IGNORECASE,
)


def notice_family_rank(record: dict) -> Optional[int]:
    """Rank an actionable SAM notice; ``None`` means not a live candidate.

    Unknown legacy fixtures remain eligible at the lowest priority so this
    classifier does not retroactively erase records that predate stored notice
    type. Award/justification notices and generic special notices are evidence,
    not open client opportunities.
    """
    raw = record.get("raw_payload") or {}
    notice_type = str(
        record.get("notice_type") or record.get("type")
        or raw.get("type") or ""
    ).strip().casefold()
    title = str(record.get("title") or "")
    if not notice_type:
        return 3
    if "award" in notice_type or "justification" in notice_type \
            or notice_type in {"j&a", "ja"}:
        return None
    if "combined synopsis" in notice_type or notice_type == "solicitation":
        return 0
    if "presolicitation" in notice_type or "sources sought" in notice_type:
        return 1
    if "special notice" in notice_type:
        return 2 if _SPECIAL_ACTIONABLE_TITLE.search(title) else None
    return 3
