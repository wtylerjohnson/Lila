"""VA Forecast of Contracting Opportunities (FCO) adapter (A8, 2026-08-18).

The VA forecast lives on the eVP vendor portal
(www.vendorportal.ecms.va.gov; the bare host does not resolve). The search
form renders anonymously, but every route to actual rows is session-gated,
measured 2026-08-18 with three independent shapes:

  1. a scripted POST of the Entire VA search form bounces to
     /eVP/Login.aspx;
  2. the in-browser async postback answers 200 with a button-only
     UpdatePanel re-render and no results;
  3. a full non-AJAX browser postback re-renders the empty form, and every
     ReportViewer.aspx?Type=...&ID=...&Year=... variant answers HTTP 302 to
     the login page.

This adapter is therefore a NAMED, receipted gap, never a silent skip: it
probes the anonymous form to prove reachability, reports failed provenance
with the receipts above, and returns no records. When the operator supplies
a vendor-portal session or VA republishes an open format, the fetch lands
here and the family contract is already wired.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from tools.api import _http
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source

SOURCE_NAME = "va_fco"
FORM_URL = ("https://www.vendorportal.ecms.va.gov/eVP/fco/"
            "EntireVA.aspx?Type=Complete")
LOGIN_URL = "https://www.vendorportal.ecms.va.gov/eVP/Login.aspx"
MEASURED = "2026-08-18"
RECEIPTS = (
    "scripted POST of the search form bounces to /eVP/Login.aspx",
    "in-browser async postback returns a button-only UpdatePanel, no rows",
    "full non-AJAX browser postback re-renders the empty form",
    "ReportViewer.aspx state/year variants answer HTTP 302 to login",
)


@register_source
class VaFcoForecast(DataSource):
    """Session-gated source, receipted honestly on every pull."""

    name = SOURCE_NAME
    kind = SourceKind.ENRICHMENT
    last_provenance: Optional[dict] = None

    def search(self, query: SourceQuery):  # enrichment-only adapter
        raise NotImplementedError("va_fco is consumed via forecasts()")

    def healthcheck(self) -> tuple[bool, str]:
        try:
            _http.get_text(FORM_URL, timeout=20.0, retries=1)
        except Exception as exc:  # noqa: BLE001 - probe, never a raise
            return False, f"form unreachable: {type(exc).__name__}"
        return False, ("form reachable but results are login-walled "
                       f"(measured {MEASURED})")

    def forecasts(self, query: Optional[SourceQuery] = None) -> list:
        reachable = True
        probe_error = None
        try:
            _http.get_text(FORM_URL, timeout=20.0, retries=1)
        except Exception as exc:  # noqa: BLE001
            reachable = False
            probe_error = f"{type(exc).__name__}: {exc}"
        self.last_provenance = {
            "source": SOURCE_NAME,
            "status": "failed",
            "mode": "login_walled",
            "complete": False,
            "records": 0,
            "source_url": FORM_URL,
            "login_url": LOGIN_URL,
            "form_reachable_anonymously": reachable,
            "probe_error": probe_error,
            "measured": MEASURED,
            "receipts": list(RECEIPTS),
            "classification": "agency forecast; never an active notice",
            "limitations": [
                "VA FCO rows are session-gated on the eVP vendor portal; "
                "no anonymous route returned data. A named gap, never a "
                "silent skip."],
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
        }
        return []
