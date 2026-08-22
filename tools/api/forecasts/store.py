"""Global forecast-record store + change detection (per source, not per client).

Every pull upserts records keyed on (source, source_id) with a content hash,
first_seen, and last_seen. Re-pulls diff against the store and classify signal
events the Developing Horizon consumes:

  - date_moved_closer  · the agency now says it will solicit SOONER
  - date_slipped       · timing moved out (still a signal; windows re-open)
  - value_grew         · the stated dollar band went up a rank
  - disappeared        · the line vanished (often means it went to solicitation)
  - new                · first appearance

Records here are TIER=PROGRAM evidence: agency-stated intent, never live
opportunities. The store is market state (client-independent); the per-client
matched-subset snapshots in snapshots.py stay as the client-facing diff.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Optional

from agents.schemas import ForecastRecord

_DEFAULT = Path(__file__).resolve().parents[3] / "data" / "state" / "forecast_store"

_HASH_FIELDS = ("title", "description", "naics_code", "psc",
                "estimated_value_range", "anticipated_solicitation",
                "anticipated_solicitation_close", "anticipated_award",
                "set_aside", "award_type", "incumbent_stated",
                "predecessor_contract_id", "forecast_status", "component",
                "source_fields")


def _root() -> Path:
    return Path(os.environ.get("LILA_FORECAST_STORE_DIR", str(_DEFAULT)))


def record_hash(rec: ForecastRecord) -> str:
    basis = "\x1f".join(str(getattr(rec, f, None) or "") for f in _HASH_FIELDS)
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


# ── timing + value ordinals (comparable across formats) ──────────────────────

_QUARTER_RE = re.compile(r"(?:FY\s*)?(?:Q([1-4])\s*(?:FY\s*)?(\d{4})|(\d{4})\s*Q([1-4]))", re.I)
_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_MONEY_RE = re.compile(r"\$?\s*([\d.]+)\s*([KMB])", re.I)
_MULT = {"K": 1e3, "M": 1e6, "B": 1e9}
_VALUE_TOKEN_RE = re.compile(
    r"(?:USD\s*)?(?:\$\s*)?"
    r"(?P<number>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<scale>K|M|B|T|thousand|million|billion|trillion)?",
    re.I,
)
_VALUE_MULTIPLIER = {
    "K": 1e3,
    "M": 1e6,
    "B": 1e9,
    "T": 1e12,
    "THOUSAND": 1e3,
    "MILLION": 1e6,
    "BILLION": 1e9,
    "TRILLION": 1e12,
}
_RANGE_SEPARATOR_RE = re.compile(r"\s+(?:to|through)\s+|\s*[-\u2013\u2014]\s*", re.I)
_UPPER_BOUND_RE = re.compile(
    r"(?:below|under|less\s+than|up\s+to|not\s+more\s+than)\s+(.+)",
    re.I,
)
_LOWER_BOUND_RE = re.compile(
    r"(?:over|above|more\s+than|at\s+least|minimum(?:\s+of)?|starting\s+at)\s+(.+)",
    re.I,
)
_SYMBOLIC_BOUND_RE = re.compile(r"(?P<operator>>=|>|<=|<)\s*(?P<amount>.+)")


def timing_ordinal(stated: Optional[str]) -> Optional[int]:
    """Comparable day-ordinal for '2026-09-30' or 'Q4 2026' style timing.
    Fiscal quarters map to the quarter's last month; unknown -> None."""
    if not stated:
        return None
    m = _DATE_RE.search(stated)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).toordinal()
        except ValueError:
            return None
    m = _QUARTER_RE.search(stated)
    if m:
        q = int(m.group(1) or m.group(4))
        fy = int(m.group(2) or m.group(3))
        # federal FY: Q1 ends Dec 31 of FY-1; Q2 Mar 31; Q3 Jun 30; Q4 Sep 30
        month, year = {1: (12, fy - 1), 2: (3, fy), 3: (6, fy), 4: (9, fy)}[q]
        return date(year, month, 28).toordinal()
    return None


def value_rank(band: Optional[str]) -> Optional[float]:
    """Lower bound of a stated dollar band ('$5M to $10M' -> 5e6)."""
    if not band:
        return None
    m = _MONEY_RE.search(band)
    if not m:
        return None
    return float(m.group(1)) * _MULT[m.group(2).upper()]


def _explicit_money_value(token: str) -> Optional[float]:
    """Parse one explicitly monetary token without supplying missing units."""

    text = token.strip()
    match = _VALUE_TOKEN_RE.fullmatch(text)
    if match is None:
        return None
    scale = match.group("scale")
    # A bare short number is too ambiguous to turn into a dollar figure. A
    # currency marker, magnitude suffix, or thousands separator makes the
    # source's monetary intent explicit within this value field.
    if "$" not in text and "USD" not in text.upper() and not scale \
            and "," not in match.group("number"):
        return None
    number = float(match.group("number").replace(",", ""))
    multiplier = _VALUE_MULTIPLIER.get((scale or "").upper(), 1.0)
    return number * multiplier


def stated_value_bounds(stated: Optional[str]) -> dict[str, float]:
    """Return only numeric bounds explicitly present in a source value string.

    The original ``estimated_value_range`` remains the authoritative display
    value. Closed ranges carry both bounds; open-ended phrases carry only the
    stated boundary. Unknown, approximate, or malformed prose carries neither,
    so downstream figure logic cannot turn it into invented pipeline dollars.
    """

    if not isinstance(stated, str) or not stated.strip():
        return {}
    text = stated.strip()
    symbolic_parts = [part.strip() for part in re.split(r"\s+and\s+", text, flags=re.I)]
    if symbolic_parts and all(
        _SYMBOLIC_BOUND_RE.fullmatch(part) for part in symbolic_parts
    ):
        bounds: dict[str, float] = {}
        for part in symbolic_parts:
            symbolic = _SYMBOLIC_BOUND_RE.fullmatch(part)
            assert symbolic is not None
            amount = _explicit_money_value(symbolic.group("amount"))
            if amount is None:
                return {}
            if symbolic.group("operator") in {">", ">="}:
                if "estimated_value_lower" in bounds:
                    return {}
                bounds["estimated_value_lower"] = amount
            else:
                if "estimated_value_upper" in bounds:
                    return {}
                bounds["estimated_value_upper"] = amount
        if (
            "estimated_value_lower" in bounds
            and "estimated_value_upper" in bounds
            and bounds["estimated_value_lower"] > bounds["estimated_value_upper"]
        ):
            return {}
        return bounds
    upper_match = _UPPER_BOUND_RE.fullmatch(text)
    if upper_match:
        upper = _explicit_money_value(upper_match.group(1))
        return {"estimated_value_upper": upper} if upper is not None else {}
    lower_match = _LOWER_BOUND_RE.fullmatch(text)
    if lower_match:
        lower = _explicit_money_value(lower_match.group(1))
        return {"estimated_value_lower": lower} if lower is not None else {}
    parts = _RANGE_SEPARATOR_RE.split(text)
    if len(parts) == 2:
        lower = _explicit_money_value(parts[0])
        upper = _explicit_money_value(parts[1])
        if lower is None or upper is None or lower > upper:
            return {}
        return {
            "estimated_value_lower": lower,
            "estimated_value_upper": upper,
        }
    if len(parts) != 1:
        return {}
    exact = _explicit_money_value(text)
    if exact is None:
        return {}
    return {
        "estimated_value_lower": exact,
        "estimated_value_upper": exact,
    }


def record_payload(rec: ForecastRecord) -> dict:
    """Serialize a forecast and attach derivable, source-stated value bounds."""

    row = json.loads(rec.model_dump_json())
    row.update(stated_value_bounds(rec.estimated_value_range))
    return row


# ── store ────────────────────────────────────────────────────────────────────

def load_store(source: str) -> dict:
    p = _root() / f"{source}.json"
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def upsert(source: str, records: list[ForecastRecord],
           today: Optional[str] = None) -> tuple[dict, list[dict]]:
    """Upsert a pull into the store; returns (store, signal_events).

    Every record gets record_hash/first_seen/last_seen filled. Events classify
    what changed since the last pull; on the first pull ever, no events (a
    baseline is not a signal)."""
    today = today or date.today().isoformat()
    store = load_store(source)
    first_pull = not store
    events: list[dict] = []
    seen_ids = set()

    for rec in records:
        rec.record_hash = record_hash(rec)
        key = rec.source_id
        seen_ids.add(key)
        prev = store.get(key)
        rec.first_seen = (prev or {}).get("first_seen") or today
        rec.last_seen = today
        row = record_payload(rec)
        if prev is None:
            if not first_pull:
                events.append({"kind": "new", "id": key, "title": rec.title,
                               "url": rec.url,
                               "agency": rec.agency,
                               "component": rec.component,
                               "detail": f"new forecast line: {rec.title}"})
        elif prev.get("record_hash") != rec.record_hash:
            was_t = timing_ordinal(prev.get("anticipated_solicitation"))
            now_t = timing_ordinal(rec.anticipated_solicitation)
            if was_t and now_t and now_t < was_t:
                events.append({
                    "kind": "date_moved_closer", "id": key, "title": rec.title,
                    "url": rec.url,
                    "agency": rec.agency, "component": rec.component,
                    "detail": (f"solicitation timing moved closer: "
                               f"{prev.get('anticipated_solicitation')} -> "
                               f"{rec.anticipated_solicitation}")})
            elif was_t and now_t and now_t > was_t:
                events.append({
                    "kind": "date_slipped", "id": key, "title": rec.title,
                    "url": rec.url,
                    "agency": rec.agency, "component": rec.component,
                    "detail": (f"solicitation timing slipped: "
                               f"{prev.get('anticipated_solicitation')} -> "
                               f"{rec.anticipated_solicitation}")})
            was_v = value_rank(prev.get("estimated_value_range"))
            now_v = value_rank(rec.estimated_value_range)
            if was_v and now_v and now_v > was_v:
                events.append({
                    "kind": "value_grew", "id": key, "title": rec.title,
                    "url": rec.url,
                    "agency": rec.agency, "component": rec.component,
                    "detail": (f"stated value band grew: "
                               f"{prev.get('estimated_value_range')} -> "
                               f"{rec.estimated_value_range}")})
        store[key] = row

    # disappearance: present before, absent now (often = went to solicitation)
    for key, prev in list(store.items()):
        if key not in seen_ids and prev.get("last_seen") != today:
            if prev.get("_disappeared"):
                continue  # already signaled
            events.append({
                "kind": "disappeared", "id": key, "title": prev.get("title"),
                "url": prev.get("url"),
                "agency": prev.get("agency"),
                "component": prev.get("component"),
                "detail": ("forecast line no longer published (often means it "
                           "went to solicitation): " + str(prev.get("title")))})
            prev["_disappeared"] = today

    os.makedirs(_root(), exist_ok=True)
    with open(_root() / f"{source}.json", "w", encoding="utf-8") as f:
        json.dump(store, f, indent=1)
    return store, events
