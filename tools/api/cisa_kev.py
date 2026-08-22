"""CISA Known Exploited Vulnerabilities adapter — live threat context (no key).

For cybersecurity clients, the strongest demand argument is what adversaries are
exploiting RIGHT NOW. CISA's KEV catalog is the government's own authoritative
list — every entry is a vulnerability with confirmed active exploitation and a
federal remediation mandate (BOD 22-01). Recent KEV velocity turns into a cited
fact the capture brief can lean on without a single speculative word.

Feed: https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json
Catalog page: https://www.cisa.gov/known-exploited-vulnerabilities-catalog
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from tools.api._http import get_json
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source
from tools.text_match import matching_phrases

FEED_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
CATALOG_URL = "https://www.cisa.gov/known-exploited-vulnerabilities-catalog"
RECENT_DAYS = 45


def _vulnerabilities(payload: Any) -> list[dict]:
    """Validate the required KEV collection without inventing a clean zero."""
    if not isinstance(payload, dict):
        raise ValueError("CISA KEV response must be an object")
    rows = payload.get("vulnerabilities")
    if not isinstance(rows, list):
        raise ValueError("CISA KEV response requires a vulnerabilities list")
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("CISA KEV vulnerabilities must contain objects")
    for row in rows:
        if not str(row.get("cveID") or "").strip():
            raise ValueError("CISA KEV vulnerability omitted cveID")
    return rows


@register_source
class CisaKevSource(DataSource):
    name = "cisa_kev"
    kind = SourceKind.ENRICHMENT

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use enrich()")

    def healthcheck(self) -> tuple[bool, str]:
        try:
            data = get_json(FEED_URL, timeout=6.0, retries=1)
            n = len(_vulnerabilities(data))
            return True, f"catalog reachable ({n} entries)"
        except Exception as e:  # noqa: BLE001
            return False, f"feed unreachable: {e}"

    def enrich(self, query: SourceQuery) -> dict[str, Any]:
        """Recent KEV velocity + entries matching the client's keyword lanes.

        Returns {'recent_count', 'window_days', 'total', 'matched': [...],
        'recent_sample': [...]} — everything cited back to the catalog page.

        ``recent_sample`` is retained as a compatibility key, but now contains
        the complete recent window. The catalog and match sets are small enough
        that silently retaining only eight records created more risk than value.
        """
        data = get_json(FEED_URL)
        vulns = _vulnerabilities(data)
        cutoff = (date.today() - timedelta(days=RECENT_DAYS)).isoformat()
        recent = [v for v in vulns if (v.get("dateAdded") or "") >= cutoff]

        kws = [k.strip('"') for k in (query.keywords or []) if k.strip('"')]
        matched = []
        if kws:
            for v in vulns:
                hay = " ".join(str(v.get(f, "")) for f in
                               ("vendorProject", "product", "vulnerabilityName",
                                "shortDescription"))
                if matching_phrases(hay, kws):
                    matched.append(v)

        def slim(v: dict) -> dict:
            return {"cve": v.get("cveID"), "vendor": v.get("vendorProject"),
                    "product": v.get("product"), "added": v.get("dateAdded"),
                    "ransomware": v.get("knownRansomwareCampaignUse")}

        return {
            "recent_count": len(recent),
            "window_days": RECENT_DAYS,
            "total": len(vulns),
            "recent_sample": [slim(v) for v in recent],
            "matched": [slim(v) for v in matched],
            "catalog_url": CATALOG_URL,
        }
