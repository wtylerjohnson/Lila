"""FPDS-NG ezsearch ATOM adapter: the contract-action stream on FPDS's own schema.

Why this source matters: every federal contract action (new award, delivery
order, IDV, modification) surfaces here hours after FPDS approval, already
carrying the join keys the pipeline speaks. PIID plus the agencyID attributes
identify the action; ns1:referencedIDVID chains a task order to its parent
vehicle PIID; ns1:solicitationID joins an award back to its SAM.gov notice;
ns1:UEI joins SAM Entity Management; baseAndExercisedOptionsValue against
baseAndAllOptionsValue exposes exercised-versus-ceiling headroom per action.
USAspending carries the same award facts only after its next-day-or-later
FPDS load, and without this keyless ad-hoc query surface.

Feed: https://www.fpds.gov/ezsearch/FEEDS/ATOM?FEEDNAME=PUBLIC&q=<query>
Pagination is a hard 10 entries per page via &start=N. This adapter caps every
pull at 3 pages (30 actions) and discloses the cap whenever the feed
advertises further pages; full-window mirroring is throttle-hostile and out
of scope by design (targeted watchlist pulls only).

Known limits, also carried in provenance limitations on every payload:
  - DoD RELEASE DELAY: DoD contract data is published only after a roughly
    90-day public-release delay (see
    https://www.fpds.gov/common/html/dodDataAvailability.html). Hours-fresh
    holds for civilian agencies; a quiet DoD window can be the delay at
    work, never proof of no activity.
  - Query field names must be spec-verified per field: a bare UEI:<value>
    prefix is NOT indexed (it returns a valid empty feed), so vendor-by-UEI
    lookups ride the free-text index instead.
  - Solicitation-ID formatting can drift from SAM notice numbers; normalize
    before joining.
  - FPDS documentation pages are 301-migrating toward sam.gov/contracting;
    the endpoint itself is legacy-stable and healthcheck() probes it live.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
from urllib.parse import urlencode

from tools.api._http import get_text
from tools.api.base import (DataSource, SourceKind, SourceQuery,
                            cap_disclosed, register_source)
from tools.api.provenance import ProvenanceEnvelope
from tools.api.source_catalog import SOURCE_BY_ADAPTER

log = logging.getLogger(__name__)

FEED_URL = "https://www.fpds.gov/ezsearch/FEEDS/ATOM"
DOD_DELAY_URL = "https://www.fpds.gov/common/html/dodDataAvailability.html"
PAGE_SIZE = 10
MAX_PAGES = 3
MAX_ACTIONS = PAGE_SIZE * MAX_PAGES
MODE = "live_fpds_atom"

_ATOM = "{http://www.w3.org/2005/Atom}"
_FPDS = "{https://www.fpds.gov/FPDS}"

DOD_DELAY_LIMITATION = (
    "DoD contract actions reach the public FPDS feed only after a roughly "
    "90-day release delay (" + DOD_DELAY_URL + "); civilian actions stream "
    "hours-fresh, so a quiet DoD window may be the delay, not inactivity"
)
UEI_FREE_TEXT_LIMITATION = (
    "UEI matched via free text: the UEI: field prefix is not indexed by "
    "ezsearch (it returns a valid empty feed), so rows may include any "
    "action whose record text carries the UEI"
)


def feed_page_url(q: str, start: int = 0) -> str:
    """One ATOM page URL for a raw ezsearch q term (10 entries per page)."""
    return FEED_URL + "?" + urlencode(
        {"FEEDNAME": "PUBLIC", "q": q, "start": start})


def _text(node: Optional[ET.Element], path: str) -> Optional[str]:
    if node is None:
        return None
    found = node.find(path)
    if found is None or found.text is None:
        return None
    return found.text.strip() or None


def _attr(node: Optional[ET.Element], name: str) -> Optional[str]:
    if node is None:
        return None
    value = (node.get(name) or "").strip()
    return value or None


def _money(raw: Optional[str]) -> Optional[float]:
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _stamp(raw: Optional[str]) -> Optional[str]:
    """FPDS '2025-03-31 10:02:31' to ISO-sortable '2025-03-31T10:02:31'."""
    if not raw:
        return None
    return raw.strip().replace(" ", "T") or None


def _date_part(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    return raw.strip().split(" ")[0] or None


def normalize_entry(entry: ET.Element) -> Optional[dict]:
    """One ATOM entry to one flat action dict, or None when unusable.

    An entry is usable only if its content carries an ns1:award or ns1:IDV
    element with a non-empty PIID; anything else is dropped and counted by
    the caller, never raised.
    """
    content = entry.find(_ATOM + "content")
    body: Optional[ET.Element] = None
    kind: Optional[str] = None
    if content is not None:
        for child in content:
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "award":
                body, kind = child, "award"
                break
            if tag == "IDV":
                body, kind = child, "idv"
                break
    if body is None:
        return None

    if kind == "award":
        contract_id = body.find(_FPDS + "awardID/" + _FPDS + "awardContractID")
    else:
        contract_id = body.find(_FPDS + "contractID/" + _FPDS + "IDVID")
    piid = _text(contract_id, _FPDS + "PIID")
    if not piid or contract_id is None:
        return None

    agency = contract_id.find(_FPDS + "agencyID")
    office_agency = body.find(".//" + _FPDS + "contractingOfficeAgencyID")
    ref_idv = body.find(_FPDS + "awardID/" + _FPDS + "referencedIDVID")
    naics = body.find(".//" + _FPDS + "principalNAICSCode")
    psc = body.find(".//" + _FPDS + "productOrServiceCode")
    dollars = body.find(_FPDS + "dollarValues")
    link = entry.find(_ATOM + "link")
    last_modified = _stamp(
        _text(body, ".//" + _FPDS + "transactionInformation/"
              + _FPDS + "lastModifiedDate")
        or entry.findtext(_ATOM + "modified")
    )
    return {
        "kind": kind,
        "piid": piid,
        "mod_number": _text(contract_id, _FPDS + "modNumber"),
        "transaction_number": _text(contract_id, _FPDS + "transactionNumber"),
        "agency_id": agency.text.strip() if agency is not None and agency.text else None,
        "agency_name": _attr(agency, "name"),
        "contracting_office_agency_id": (
            office_agency.text.strip()
            if office_agency is not None and office_agency.text else None),
        "contracting_office_agency_name": _attr(office_agency, "name"),
        "department_id": _attr(office_agency, "departmentID"),
        "department_name": _attr(office_agency, "departmentName"),
        "contracting_office_id": _text(
            body, ".//" + _FPDS + "contractingOfficeID"),
        "referenced_idv_piid": _text(ref_idv, _FPDS + "PIID"),
        "referenced_idv_agency_id": _text(ref_idv, _FPDS + "agencyID"),
        "solicitation_id": _text(body, ".//" + _FPDS + "solicitationID"),
        "uei": _text(body, ".//" + _FPDS + "vendorUEIInformation/"
                     + _FPDS + "UEI"),
        "uei_legal_business_name": _text(
            body, ".//" + _FPDS + "vendorUEIInformation/"
            + _FPDS + "UEILegalBusinessName"),
        "vendor_name": _text(body, ".//" + _FPDS + "vendorHeader/"
                             + _FPDS + "vendorName"),
        "naics_code": naics.text.strip() if naics is not None and naics.text else None,
        "naics_description": _attr(naics, "description"),
        "psc_code": psc.text.strip() if psc is not None and psc.text else None,
        "psc_description": _attr(psc, "description"),
        "signed_date": _date_part(_text(
            body, _FPDS + "relevantContractDates/" + _FPDS + "signedDate")),
        "obligated_amount": _money(_text(
            dollars, _FPDS + "obligatedAmount")),
        "base_and_exercised_options_value": _money(_text(
            dollars, _FPDS + "baseAndExercisedOptionsValue")),
        "base_and_all_options_value": _money(_text(
            dollars, _FPDS + "baseAndAllOptionsValue")),
        "last_modified": last_modified,
        "action_title": " ".join((entry.findtext(_ATOM + "title") or "").split()) or None,
        "citation_url": _attr(link, "href"),
    }


def parse_feed_page(xml_text: str) -> tuple[list[dict], int, bool]:
    """Parse one ATOM page: (actions, malformed_dropped, next_advertised).

    Raises ValueError when the document is not an ATOM feed at all (for
    example a WAF or maintenance HTML page, which IS well-formed XML); an
    empty result must never be counterfeited by a block page.
    """
    root = ET.fromstring(xml_text)
    if root.tag != _ATOM + "feed":
        raise ValueError("root element is <%s>, not an ATOM feed" % root.tag)
    actions: list = []
    dropped = 0
    for entry in root.findall(_ATOM + "entry"):
        row = normalize_entry(entry)
        if row is None:
            dropped += 1
        else:
            actions.append(row)
    next_advertised = any(
        link.get("rel") == "next" for link in root.findall(_ATOM + "link"))
    return actions, dropped, next_advertised


class FpdsAtomSource(DataSource):
    name = "fpds_atom"
    kind = SourceKind.ENRICHMENT

    def __init__(self, fetch: Optional[Callable[..., str]] = None) -> None:
        #: fetch(url, *, timeout=..., retries=...) -> str; tests inject a fake
        self._fetch = fetch or get_text

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError(
            "enrichment-only source; use actions_for_piid(), "
            "actions_for_uei(), or recent_actions()")

    def healthcheck(self) -> tuple[bool, str]:
        """Live probe: one page of yesterday-to-today LAST_MOD_DATE actions."""
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=1)
        url = feed_page_url(_last_mod_query(start, end))
        try:
            actions, _dropped, _more = parse_feed_page(
                self._fetch(url, timeout=8.0, retries=1))
            return True, (
                "feed reachable (%d actions on first probe page)" % len(actions))
        except Exception as exc:  # noqa: BLE001
            return False, "FPDS ATOM feed unreachable: %s" % exc

    # ------------------------------------------------------------------ #
    # Enrichment surface (consumed by engines)
    # ------------------------------------------------------------------ #
    def actions_for_piid(self, piid: str) -> dict[str, Any]:
        """Every action filed under one PIID (base action plus mods)."""
        cleaned = (piid or "").strip()
        if not cleaned:
            return self._failed_payload("", FEED_URL, "empty PIID")
        return self._pull('PIID:"%s"' % cleaned)

    def actions_for_uei(self, uei: str) -> dict[str, Any]:
        """A vendor's actions by UEI, via the free-text index.

        The UEI: field prefix is NOT indexed by ezsearch (a bare
        UEI:<value> query returns a valid empty feed), so this rides free
        text; the limitation is disclosed on the payload.
        """
        cleaned = (uei or "").strip()
        if not cleaned:
            return self._failed_payload("", FEED_URL, "empty UEI")
        return self._pull(
            cleaned, extra_limitations=[UEI_FREE_TEXT_LIMITATION])

    def recent_actions(self, query: SourceQuery, days: int = 7) -> dict[str, Any]:
        """Actions modified in the trailing window, NAICS-scoped when given.

        Builds q=LAST_MOD_DATE:[start,end] plus a PRINCIPAL_NAICS_CODE term
        from the first NAICS code on the SourceQuery; other query fields the
        feed cannot express are ignored, per the SourceQuery doctrine.
        """
        window = max(1, int(days))
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=window)
        q = _last_mod_query(start, end)
        naics = next(
            (code.strip() for code in (query.naics_codes or [])
             if code and code.strip()), None)
        if naics:
            q += ' PRINCIPAL_NAICS_CODE:"%s"' % naics
        return self._pull(q)

    # ------------------------------------------------------------------ #
    # Shared pull engine
    # ------------------------------------------------------------------ #
    def _pull(self, q: str,
              extra_limitations: Optional[list] = None) -> dict[str, Any]:
        retrieved_at = datetime.now(timezone.utc)
        first_url = feed_page_url(q, start=0)
        actions: list = []
        dropped = 0
        pages_fetched = 0
        next_advertised = False
        error: Optional[str] = None

        for page in range(MAX_PAGES):
            url = feed_page_url(q, start=page * PAGE_SIZE)
            try:
                xml_text = self._fetch(url, timeout=30.0, retries=2)
            except Exception as exc:  # noqa: BLE001
                error = "page %d fetch failed: %s" % (page + 1, exc)
                log.warning("fpds_atom: %s", error)
                break
            try:
                page_actions, page_dropped, next_advertised = parse_feed_page(
                    xml_text)
            except (ET.ParseError, ValueError) as exc:
                error = ("page %d was not a parseable ATOM feed: %s"
                         % (page + 1, exc))
                log.warning("fpds_atom: %s", error)
                break
            actions.extend(page_actions)
            dropped += page_dropped
            pages_fetched += 1
            if not next_advertised:
                break

        if error and pages_fetched == 0:
            return self._failed_payload(
                q, first_url, error, extra_limitations)

        capped_by_pages = pages_fetched >= MAX_PAGES and next_advertised
        payload = cap_disclosed(actions, MAX_ACTIONS, key="actions")
        if capped_by_pages:
            payload["truncated"] = True
            payload["truncated_note"] = (
                "page cap %d (%d actions) reached with further pages "
                "advertised by the feed; total_matched is a lower bound"
                % (MAX_PAGES, MAX_ACTIONS))

        limitations = [DOD_DELAY_LIMITATION]
        limitations.extend(extra_limitations or [])
        if capped_by_pages:
            limitations.append(str(payload["truncated_note"]))
        if dropped:
            limitations.append(
                "%d malformed feed entries dropped" % dropped)
        if error:
            limitations.append("partial pull: " + error)

        status = "complete"
        if error or capped_by_pages or dropped:
            status = "partial"

        data_as_of = None
        stamps = [a["last_modified"] for a in actions if a.get("last_modified")]
        if stamps:
            data_as_of = max(stamps)

        payload.update({
            "query": q,
            "feed_url": first_url,
            "pages_fetched": pages_fetched,
            "malformed_dropped": dropped,
        })
        if error:
            payload["error"] = error
        payload["_provenance"] = ProvenanceEnvelope(
            source=self.name,
            status=status,
            mode=MODE,
            retrieval_mode="live",
            retrieved_at=retrieved_at,
            data_as_of=data_as_of,
            record_count=len(actions),
            limitations=limitations,
        ).model_dump(mode="json")
        return payload

    def _failed_payload(self, q: str, url: str, error: str,
                        extra_limitations: Optional[list] = None,
                        ) -> dict[str, Any]:
        limitations = [DOD_DELAY_LIMITATION]
        limitations.extend(extra_limitations or [])
        return {
            "actions": [],
            "total_matched": 0,
            "query": q,
            "feed_url": url,
            "pages_fetched": 0,
            "malformed_dropped": 0,
            "error": error,
            "_provenance": ProvenanceEnvelope(
                source=self.name,
                status="failed",
                mode=MODE,
                retrieval_mode="live",
                retrieved_at=datetime.now(timezone.utc),
                record_count=0,
                limitations=limitations,
            ).model_dump(mode="json"),
        }


def _last_mod_query(start, end) -> str:
    return "LAST_MOD_DATE:[%s,%s]" % (
        start.strftime("%Y/%m/%d"), end.strftime("%Y/%m/%d"))


def _register_when_cataloged() -> None:
    """Activate @register_source once the catalog knows this adapter.

    register_source refuses adapters without a SourceSpec row, and
    tools/api/source_catalog.py is a contract surface this connector lane
    does not edit. The lead lands the SourceSpec plus the registrar import;
    this guard makes that single catalog addition activate registration
    with no edit here, and keeps the module importable until then.
    """
    if FpdsAtomSource.name in SOURCE_BY_ADAPTER:
        register_source(FpdsAtomSource)


_register_when_cataloged()
