"""Turn a notice into ContactObservations — the one place POC parsing lives.

Used by both harvest paths (inline sweep + backfill). Accepts, in order of
richness:
  1. a raw SAM opportunitiesData dict (has `pointOfContact` at top level),
  2. a serialized RawOpportunity dict (has `raw_payload` — the original notice —
     and/or a parsed `contacts` list),
  3. a RawOpportunity pydantic instance (converted via model_dump()).

`raw_payload.pointOfContact` is preferred wherever present because it carries the
full field set (type / fax); the parsed `contacts` list is the fallback. Every
POC on the notice is captured, not just the first — secondary POCs and contract
specialists are often the reachable ones. A POC published with both email and
phone yields one observation per channel; a named POC with no channel yields a
single channel-less sighting so it still counts toward rotation/first-seen.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional

from tools.contact_graph.schemas import UNATTRIBUTED, ChannelKind, ContactObservation

# Agency/office paths arrive with two delimiters in the wild: raw SAM uses '.'
# ("COMMERCE, DEPARTMENT OF.NOAA..."), the cleaned artifacts use ' / '
# ("COMMERCE, DEPARTMENT OF / NIST"). The top-level department is the first
# segment under either.
_PATH_SPLIT = re.compile(r"\s*/\s*|\.")

# ---- description-text channel patterns (conservative on purpose) -----------
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
# US phone: requires visible formatting — (xxx) xxx-xxxx or xxx-xxx-xxxx or
# xxx.xxx.xxxx, optional +1. A bare 10-digit run is NOT matched: in body text
# that is as likely a solicitation number as a phone.
_PHONE_RE = re.compile(r"(?:\+?1[\s.\-])?(?:\(\d{3}\)\s?|\d{3}[.\-])\d{3}[.\-]\d{4}\b")
# A name "clearly adjacent" before a channel: a capitalized First [M.] Last
# ending within a short connective gap of the hit ("Jane Doe, x@y.gov",
# "Jane A. Doe at (301) 555-0100", "POC: Jane Doe <x@y.gov>").
_ADJACENT_NAME_RE = re.compile(
    r"([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+(?:-[A-Z][a-z]+)?)"  # First [M.] Last[-Last]
    r"(?:\s*(?:,|:|;|<|\(|\bat\b|\bvia\b|\bon\b|-|–)\s*|\s+)"        # connective
    r"$"                                                              # ...ending at the hit
)
_NAME_WINDOW = 48  # chars of text before a hit searched for an adjacent name
_URL_LIKE = re.compile(r"^https?://", re.I)


def _adjacent_name(text: str, hit_start: int) -> Optional[str]:
    """The clearly adjacent published name before a channel hit, or None.
    The regex is anchored to the end of the window, so intervening prose
    between a name and the channel breaks adjacency — no guessing."""
    window = text[max(0, hit_start - _NAME_WINDOW): hit_start]
    m = _ADJACENT_NAME_RE.search(window)
    return m.group(1) if m else None


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _agency_from_path(office_path: Optional[str]) -> Optional[str]:
    if not office_path:
        return None
    first = _PATH_SPLIT.split(office_path)[0].strip()
    return first or None


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    # SAM posts either 'YYYY-MM-DD' or full ISO ('YYYY-MM-DDThh:mm:ss[-zz]').
    for candidate in (text, text[:10]):
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _pocs_from(record: dict, payload: dict) -> list[dict]:
    """Return POC dicts in SAM shape (fullName/title/type/email/phone), preferring
    the raw payload's pointOfContact, else translating a parsed `contacts` list."""
    raw = payload.get("pointOfContact")
    if isinstance(raw, list) and raw:
        return [c for c in raw if isinstance(c, dict)]
    contacts = record.get("contacts")
    if isinstance(contacts, list) and contacts:
        # OpportunityContact shape -> SAM POC shape
        return [
            {
                "fullName": c.get("name"),
                "title": c.get("title"),
                "type": c.get("contact_type"),
                "email": c.get("email"),
                "phone": c.get("phone"),
            }
            for c in contacts
            if isinstance(c, dict)
        ]
    return []


def _links(record: dict, payload: dict, notice_id: str) -> tuple[str, bool, str, bool]:
    """(source_url, source_url_derived, api_ref, api_ref_derived).

    Every observation carries BOTH the human-viewable sam.gov URL and the API
    reference. SAM search records provide uiLink (human) and a `description`
    field that is itself the noticedesc API link; whichever form the cached
    data lacks is derived from the notice ID and flagged as derived.
    """
    candidates = [_clean(payload.get("uiLink")), _clean(record.get("api_url"))]
    ui = next((c for c in candidates if c and "api.sam.gov" not in c), None)
    ui_derived = ui is None
    if ui is None:
        ui = f"https://sam.gov/opp/{notice_id}/view"

    desc = payload.get("description")
    api = desc if isinstance(desc, str) and "api.sam.gov" in desc else None
    api_derived = api is None
    if api is None:
        api = f"https://api.sam.gov/opportunities/v2/noticedesc?noticeid={notice_id}"
    return ui, ui_derived, api, api_derived


def _description_text(record: dict, payload: dict) -> str:
    """The notice body text, if cached data carries it. In search records
    `description` is a URL (the API link, not text) — only real prose counts."""
    for container in (payload, record):
        desc = container.get("description")
        if isinstance(desc, str) and desc.strip() and not _URL_LIKE.match(desc.strip()):
            return desc
    return ""


def observations_from_notice(
    notice: Any,
    *,
    source: str = "sam.gov",
    harvested_at: datetime,
    default_posted: Optional[date] = None,
) -> list[ContactObservation]:
    """Extract every contact channel on one notice as ContactObservations.

    Two extraction surfaces, in trust order:
      1. structured pointOfContact fields (channel_source="poc_field"),
      2. conservative email/US-phone pattern matches in the description body
         (channel_source="description_text") — attributed to a name only when
         one is clearly adjacent, else recorded as UNATTRIBUTED for human
         review. Channels already captured from POC fields are not repeated.

    `default_posted` is used as observed_at when the notice itself carries no
    posted date (backfill may know a date from surrounding context).
    Returns [] for a notice with no usable contact data — callers treat that
    as a coverage gap, they do not fail.
    """
    record = notice.model_dump() if hasattr(notice, "model_dump") else dict(notice or {})
    payload = record.get("raw_payload")
    if not isinstance(payload, dict) or not payload:
        payload = record

    notice_id = (
        _clean(payload.get("noticeId"))
        or _clean(record.get("source_id"))
        or _clean(record.get("id"))  # permanent notice/detail cache shape
    )
    if not notice_id:
        return []

    office_path = _clean(payload.get("fullParentPathName")) or _clean(record.get("agency"))
    agency = _agency_from_path(office_path)
    naics = _clean(payload.get("naicsCode")) or _clean(record.get("naics_code"))
    notice_type = _clean(payload.get("type")) or _clean(record.get("notice_type"))
    source_url, source_url_derived, api_ref, api_ref_derived = _links(record, payload, notice_id)
    observed_at = (
        _parse_date(payload.get("postedDate"))
        or _parse_date(record.get("posted_date"))
        or default_posted
    )

    def _make(name, kind, value, *, role_type=None, title=None, channel_source="poc_field"):
        return ContactObservation(
            source=source,
            notice_id=notice_id,
            person_name=name,
            channel_kind=kind,
            channel_value=value,
            channel_source=channel_source,
            role_type=role_type,
            title=title,
            agency=agency,
            office_path=office_path,
            notice_type=notice_type,
            naics=naics,
            source_url=source_url,
            source_url_derived=source_url_derived,
            api_ref=api_ref,
            api_ref_derived=api_ref_derived,
            observed_at=observed_at,
            harvested_at=harvested_at,
        )

    out: list[ContactObservation] = []
    seen_emails: set[str] = set()
    seen_phones: set[str] = set()

    # --- surface 1: structured POC fields -----------------------------------
    for poc in _pocs_from(record, payload):
        name = _clean(poc.get("fullName"))
        email = _clean(poc.get("email"))
        phone = _clean(poc.get("phone"))
        # An all-null stub (no name and no channel) carries no information.
        if not name and not email and not phone:
            continue

        channels: list[tuple[Optional[ChannelKind], Optional[str]]] = []
        if email:
            channels.append(("email", email))
            seen_emails.add(email.lower())
        if phone:
            channels.append(("phone", phone))
            seen_phones.add(_digits(phone))
        if not channels:
            channels.append((None, None))  # named sighting, no reachable channel

        for kind, value in channels:
            out.append(_make(name, kind, value,
                             role_type=_clean(poc.get("type")),
                             title=_clean(poc.get("title"))))

    # --- surface 2: description body text (conservative patterns) -----------
    text = _description_text(record, payload)
    if text:
        for m in _EMAIL_RE.finditer(text):
            value = m.group(0)
            if value.lower() in seen_emails:
                continue  # already captured from the structured field
            seen_emails.add(value.lower())
            name = _adjacent_name(text, m.start()) or UNATTRIBUTED
            out.append(_make(name, "email", value, channel_source="description_text"))
        for m in _PHONE_RE.finditer(text):
            value = m.group(0)
            if _digits(value) in seen_phones:
                continue
            seen_phones.add(_digits(value))
            name = _adjacent_name(text, m.start()) or UNATTRIBUTED
            out.append(_make(name, "phone", value, channel_source="description_text"))

    return out
