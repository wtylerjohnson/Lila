"""SBA Small Business Search adapter: certified-firm capability + contact lookup.

The teaming and competitive layers need to know WHICH certified small firms
claim a capability, with living contacts. SBS (successor to the retired DSBS)
is SBA's own directory of certified and self-certified firms, and it carries
what no pipeline source has: firm-authored capability narratives and keyword
tags, contact_person/email/phone (the prior SBA-directory lane measured ZERO
contacts), the full SBA cert lifecycle with entrance/exit dates (8(a) exits
are graduation timing signals), and UEI + CAGE first-class on every record,
so results join straight to SAM Entity Management and USAspending.

Feed: POST https://search.certifications.sba.gov/_api/v2/search   (anonymous)
Firm page: https://search.certifications.sba.gov/profile/{uei}/{cage_code}

The API is internal to the React app: no docs, no ToS, no pagination. The
request body must be the app's FULL default filters object or the server
answers 500, and an unscoped body returns the entire public directory in one
response (7,024 records observed as 33.6 MB), so this adapter refuses
unscoped pulls and always sends the exact default shape pinned from the app
bundle (static/js/main.9d375dc3.js, read 2026-08-08; operatorType "Or",
relationOperator "at-least", lastUpdated date {label "Anytime", value
"anytime"}, naics/cert entries as {label, value} objects). The server
injects the Meilisearch filter "public_display = true": firms that opted out
of public display are invisible, and that limit rides in every payload's
provenance envelope.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from tools.api._http import post_json
from tools.api.base import (DataSource, SourceKind, SourceQuery,
                            cap_disclosed, register_source)
from tools.api.provenance import ProvenanceEnvelope

SEARCH_URL = "https://search.certifications.sba.gov/_api/v2/search"
APP_URL = "https://search.certifications.sba.gov/"
PROFILE_URL = "https://search.certifications.sba.gov/profile/{uei}/{cage}"
#: nonsense probe term: proves reachability AND that the body contract still
#: parses (contract drift returns 500) while the response stays tiny.
HEALTHCHECK_TERM = "lilaprobe zzqxv"
NARRATIVE_HEAD_CHARS = 240
MAX_FIRM_KEYWORDS = 8
MAX_SMALL_NAICS = 12
PUBLIC_DISPLAY_LIMIT = (
    "server injects Meilisearch filter public_display = true; firms that "
    "opted out of public display are invisible to this source"
)
UNDOCUMENTED_API_LIMIT = (
    "undocumented internal app API; request body pinned to the app bundle "
    "default filters object and may drift with any front-end deploy"
)


def _default_filters() -> dict:
    """The app's exact default request body (main.9d375dc3.js, 2026-08-08).

    Anything less than this full shape gets HTTP 500 from the server, so
    scoping means overwriting fields inside this object, never omitting them.
    """
    return {
        "searchProfiles": {"searchTerm": ""},
        "location": {"states": [], "zipCodes": [], "counties": [],
                     "districts": [], "msas": []},
        "sbaCertifications": {"activeCerts": [], "isPreviousCert": False,
                              "operatorType": "Or"},
        "naics": {"codes": [], "isPrimary": False, "operatorType": "Or"},
        "selfCertifications": {"certifications": [], "operatorType": "Or"},
        "keywords": {"list": [], "operatorType": "Or"},
        "lastUpdated": {"date": {"label": "Anytime", "value": "anytime"}},
        "samStatus": {"isActiveSAM": False},
        "qualityAssuranceStandards": {"qas": []},
        "bondingLevels": {"constructionIndividual": "",
                          "constructionAggregate": "",
                          "serviceIndividual": "", "serviceAggregate": ""},
        "businessSize": {"relationOperator": "at-least",
                         "numberOfEmployees": ""},
        "annualRevenue": {"relationOperator": "at-least",
                          "annualGrossRevenue": ""},
        "entityDetailId": "",
    }


def _build_body(query: SourceQuery) -> dict:
    """Translate the shared SourceQuery into the app's filters object.

    Keywords ride the full-profile search term (narratives, keyword tags,
    names); NAICS codes become {label, value} entries matched against any
    NAICS on the firm (isPrimary stays False so a firm listing the code as
    secondary still surfaces). Fields SBS cannot express are ignored.
    """
    body = _default_filters()
    term = query.title_contains()
    if term:
        body["searchProfiles"]["searchTerm"] = term
    if query.naics_codes:
        body["naics"]["codes"] = [
            {"label": code, "value": code} for code in query.naics_codes
        ]
    return body


def _iso_date_from_epoch(value: Any) -> Optional[str]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _slim_firm(rec: Any) -> dict:
    """Project one 123-field SBS record onto the decision-relevant slice.

    Raises on records that are not objects or carry neither a name nor a
    UEI; the caller drops those and counts them instead of crashing.
    """
    if not isinstance(rec, dict):
        raise TypeError("record is not a JSON object")
    name = rec.get("legal_business_name") or rec.get("dba_name")
    uei = rec.get("uei")
    if not name and not uei:
        raise ValueError("record carries neither a business name nor a UEI")

    certs = []
    for cert in rec.get("certs") or []:
        if isinstance(cert, dict) and cert.get("name"):
            certs.append({
                "name": cert.get("name"),
                "status": cert.get("status"),
                "active": bool(cert.get("active")),
                "entrance_date": cert.get("entranceDate"),
                "exit_date": cert.get("exitDate"),
            })

    raw_keywords = rec.get("keywords")
    keywords = []
    if isinstance(raw_keywords, list):
        keywords = [str(k) for k in raw_keywords[:MAX_FIRM_KEYWORDS] if k]

    small = rec.get("naics_small_codes")
    naics_small = []
    if isinstance(small, list):
        naics_small = [str(c) for c in small[:MAX_SMALL_NAICS] if c]

    narrative = str(rec.get("capabilities_narrative") or "").strip()
    cage = rec.get("cage_code")
    profile_url = None
    if uei and cage:
        profile_url = PROFILE_URL.format(uei=uei, cage=cage)

    #: the API already nulls fields whose display flag is off; honoring the
    #: flag here keeps the projection honest even if that changes.
    email = rec.get("email") if rec.get("display_email") is not False else None
    phone = rec.get("phone") if rec.get("display_phone") is not False else None

    return {
        "name": name,
        "uei": uei,
        "cage": cage,
        "certs": certs,
        "naics": rec.get("naics_primary"),
        "naics_small": naics_small,
        "state": rec.get("state"),
        "capabilities_narrative_head": narrative[:NARRATIVE_HEAD_CHARS] or None,
        "capabilities_link": rec.get("capabilities_link"),
        "keywords": keywords,
        "contact": rec.get("contact_person"),
        "email": email,
        "phone": phone,
        "website": rec.get("website"),
        "last_updated": _iso_date_from_epoch(rec.get("last_update_date")),
        "profile_url": profile_url,
    }


class SbaSbsSource(DataSource):
    name = "sba_sbs"
    kind = SourceKind.ENRICHMENT

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use firms()")

    def healthcheck(self) -> tuple[bool, str]:
        body = _default_filters()
        body["searchProfiles"]["searchTerm"] = HEALTHCHECK_TERM
        try:
            resp = post_json(SEARCH_URL, json=body, timeout=8.0, retries=1)
        except Exception as exc:  # noqa: BLE001
            return False, (
                f"search endpoint unreachable or body contract drifted: {exc}"
            )
        if isinstance(resp, dict) and isinstance(resp.get("results"), list):
            return True, "search endpoint reachable; body contract accepted"
        return False, f"unexpected response shape: {str(resp)[:120]}"

    def firms(self, query: SourceQuery) -> dict[str, Any]:
        """Certified firms matching the query's keywords and/or NAICS codes.

        Returns {'firms': [...], 'total_matched': N, 'search_url': ...,
        '_provenance': ...} with each firm slimmed to name, UEI + CAGE join
        keys, cert lifecycle, NAICS, state, capability narrative head,
        capability statement link, firm-authored keywords, and contact
        fields. The endpoint returns
        every match in one response (no pagination), so the kept list is
        capped at query.limit with the cut disclosed via cap_disclosed.
        """
        if not query.keywords and not query.naics_codes:
            return self._failed_payload(
                "unscoped query refused: the endpoint returns the entire "
                "public directory in one response; pass keywords and/or "
                "naics_codes"
            )
        try:
            resp = post_json(SEARCH_URL, json=_build_body(query))
        except Exception as exc:  # noqa: BLE001
            return self._failed_payload(f"search request failed: {exc}")

        results = resp.get("results") if isinstance(resp, dict) else None
        if not isinstance(results, list):
            return self._failed_payload(
                "unexpected response shape: no results list"
            )

        rows = []
        dropped = 0
        for rec in results:
            try:
                rows.append(_slim_firm(rec))
            except Exception:  # noqa: BLE001
                dropped += 1

        payload = cap_disclosed(rows, max(1, query.limit), key="firms")
        limitations = [PUBLIC_DISPLAY_LIMIT, UNDOCUMENTED_API_LIMIT]
        if dropped:
            limitations.append(
                f"{dropped} malformed records dropped during normalization"
            )
        payload["search_url"] = SEARCH_URL
        payload["app_url"] = APP_URL
        server_filter = resp.get("meili_filter")
        if server_filter:
            payload["server_filter"] = str(server_filter)
        payload["_provenance"] = ProvenanceEnvelope(
            source=self.name,
            status="partial" if dropped else "complete",
            mode="live_search",
            retrieval_mode="live",
            retrieved_at=datetime.now(timezone.utc),
            data_as_of=None,
            record_count=len(payload["firms"]),
            limitations=limitations,
        ).model_dump(mode="json")
        return payload

    def _failed_payload(self, message: str) -> dict[str, Any]:
        """Honest failure shape: the error rides the payload, never raises."""
        return {
            "firms": [],
            "total_matched": 0,
            "error": message,
            "search_url": SEARCH_URL,
            "_provenance": ProvenanceEnvelope(
                source=self.name,
                status="failed",
                mode="live_search",
                retrieval_mode="live",
                retrieved_at=datetime.now(timezone.utc),
                record_count=0,
                limitations=[message],
            ).model_dump(mode="json"),
        }


try:
    register_source(SbaSbsSource)
except ValueError:
    # sba_sbs has no SourceSpec row in tools/api/source_catalog.py yet; the
    # integration lead wires the catalog row and the registrar import
    # together. Until then the adapter stays importable but unregistered,
    # so tests and engines that construct it directly keep working.
    pass
