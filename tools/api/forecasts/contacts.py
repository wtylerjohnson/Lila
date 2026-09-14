"""Source-published forecast contacts; no enrichment or qualification."""
from datetime import date, datetime
from urllib.parse import urlsplit

from agents.schemas import OpportunityContact

ROLES = (
    ("requirements_contact", "primary", "Primary forecast POC"),
    ("alternate_contact", "alternate", "Alternate forecast POC"),
    ("sbs_coordinator", "small_business_coordinator", "Small-business specialist / APFS coordinator"),
)


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def apfs_contacts(raw):
    contacts = []
    for prefix, role, label in ROLES:
        name = " ".join(filter(None, (_text(raw.get(prefix + "_first_name")),
                                     _text(raw.get(prefix + "_last_name")))))
        email, phone = (_text(raw.get(prefix + "_" + key)) for key in ("email", "phone"))
        if name or email or phone:
            contacts.append(OpportunityContact(name=name or None, email=email or None,
                                               phone=phone or None, contact_type=role,
                                               title=label))
    return contacts


def publication_date(raw):
    """Only publication dates, never harvest/retrieval/award dates."""
    values = []
    for key in ("published_date", "publish_date"):
        value = raw.get(key)
        if not value:
            continue
        parsed = None
        for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
            try:
                parsed = datetime.strptime(str(value), fmt).date()
                break
            except ValueError:
                pass
        if parsed is None:
            return None
        values.append(parsed)
    return values[0] if values and len(set(values)) == 1 else None


def contact_record(record):
    """Return a validated display/harvest projection, including legacy APFS rows.

    APFS structured contacts are always re-derived from the retained original
    fields. A copied contact list cannot borrow another forecast's source URL.
    Other adapters can supply their own typed contacts on an official URL.
    """
    row = record.model_dump(mode="json") if hasattr(record, "model_dump") else dict(record)
    raw = row.get("source_fields") or {}
    if not isinstance(raw, dict):
        return None
    url = row.get("url") or row.get("source_url") or ""
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme != "https" or not (parsed.hostname or "").endswith((".gov", ".mil")):
        return None
    source = row.get("source") or row.get("forecast_source")
    sid = str(row.get("source_id") or row.get("record_id") or "")
    if source == "dhs_apfs" or parsed.hostname == "apfs-cloud.dhs.gov":
        source = "dhs_apfs"
        if (parsed.hostname != "apfs-cloud.dhs.gov" or not raw.get("id")
                or parsed.path.rstrip("/") != f"/record/{raw['id']}/public-print"
                or sid.lstrip("*") != str(raw.get("apfs_number") or "").lstrip("*")):
            return None
        contacts = apfs_contacts(raw)
        published = publication_date(raw)
    else:
        if not source or not sid:
            return None
        try:
            contacts = [OpportunityContact.model_validate(c) for c in row.get("contacts") or []]
        except (ValueError, TypeError):
            return None
        try:
            published = date.fromisoformat(row["contact_publication_date"])
        except (ValueError, TypeError, KeyError):
            published = None
    unique = {}
    for contact in contacts:
        if not any((contact.name, contact.email, contact.phone)):
            continue
        key = tuple((_text(v).casefold()) for v in
                    (contact.name, contact.email, contact.phone, contact.contact_type))
        unique.setdefault(key, contact)
    return {"source": source, "source_id": sid, "source_url": url,
            "agency": row.get("agency"), "component": row.get("component") or row.get("sub_agency"),
            "naics_code": row.get("naics_code") or row.get("naics"),
            "published": published, "contacts": list(unique.values())}
