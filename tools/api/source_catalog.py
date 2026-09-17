"""Deterministic source and sweep-lane catalog.

This module is the single metadata owner for collector identity, persisted
result keys, public coverage labels, evidence role, and downstream consumer.
Collection callables stay beside their credentials/query logic; the runner
reconciles those callables against this catalog before starting the fan-out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Mapping, Optional


CoverageClass = Literal[
    "required-exhaustive",
    "required-bounded",
    "advisory",
]


@dataclass(frozen=True)
class SourceSpec:
    """One stable source or aggregate runner lane.

    Identity is the name stored in provenance. A standard sweep lane also owns
    task_key and result_key. Child adapters (for example APFS beneath the
    aggregate forecasts lane) point at their parent but do not add a second
    public coverage row.
    """

    identity: str
    label: str
    family: str
    record_role: str
    consumers: tuple[str, ...]
    task_key: Optional[str] = None
    result_key: Optional[str] = None
    adapter_name: Optional[str] = None
    standard: bool = False
    enabled_default: bool = True
    scope_policy: str = "engagement-scope"
    support_level: str = "full"
    coverage_class: CoverageClass = "required-exhaustive"
    coverage_boundary: str = ""
    coverage_origins: tuple[str, ...] = ()
    parent_identity: Optional[str] = None
    connected_detail: str = ""
    official_url: str = ""
    identity_version: int = 1
    coverage_active: bool = False
    coverage_description: str = ""
    agency_codes: tuple[str, ...] = ()
    agency_hints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.identity.strip() or not self.label.strip():
            raise ValueError("source identity and label must be non-empty")
        if not self.family.strip() or not self.record_role.strip():
            raise ValueError("source family and record role must be non-empty")
        if not self.consumers or any(not value.strip() for value in self.consumers):
            raise ValueError(f"{self.identity}: at least one consumer is required")
        if self.standard and (not self.task_key or not self.result_key):
            raise ValueError(
                f"{self.identity}: standard lanes require task and result keys")
        if self.identity_version < 1:
            raise ValueError("source identity version must be positive")
        if self.support_level not in {"full", "partial"}:
            raise ValueError(
                f"{self.identity}: support level must be full or partial")
        if self.coverage_class not in {
                "required-exhaustive", "required-bounded", "advisory"}:
            raise ValueError(
                f"{self.identity}: invalid coverage class "
                f"{self.coverage_class!r}")
        if (self.coverage_class in {"required-bounded", "advisory"}
                and not self.coverage_boundary.strip()):
            raise ValueError(
                f"{self.identity}: bounded/advisory sources must declare "
                "their coverage boundary")
        if (self.coverage_class == "required-bounded"
                and not self.coverage_origins):
            raise ValueError(
                f"{self.identity}: bounded required sources must declare "
                "their required origins")
        if (self.coverage_class != "required-bounded"
                and self.coverage_origins):
            raise ValueError(
                f"{self.identity}: only bounded required sources may declare "
                "coverage origins")
        if self.coverage_active and not self.agency_codes:
            raise ValueError(
                f"{self.identity}: active forecast coverage needs agency codes")


def _mesh_child(
    identity: str,
    label: str,
    family: str,
    record_role: str,
    consumers: tuple[str, ...],
    official_url: str,
) -> SourceSpec:
    """Catalog one child of the required source-mesh family.

    The parent lane owns the all-children completeness contract. Children are
    advisory as standalone rows so the same failure is not counted twice, but
    every child still carries its own official boundary and provenance row.
    """
    return SourceSpec(
        identity,
        label,
        family,
        record_role,
        consumers,
        adapter_name=identity,
        parent_identity="source_mesh",
        coverage_class="advisory",
        coverage_boundary=(
            "Scoped official-source adapter inside the required 27-source "
            "enrichment mesh; status and record count are reported separately."
        ),
        official_url=official_url,
    )


# Standard entries are intentionally first and in exact fan-out order. New
# source families land by adding one reviewed spec and a collector callable;
# reconciliation makes either half alone a named failure.
SOURCE_SPECS: tuple[SourceSpec, ...] = (
    SourceSpec(
        "sam.gov", "SAM.gov contract opportunities", "procurement",
        "live-solicitation", ("assessment-ledger", "report-composer"),
        "sam.gov", "sam.gov", "sam.gov", standard=True,
    ),
    SourceSpec(
        "usaspending.gov", "USAspending award history", "federal-spending",
        "award-context", ("fact-pack", "report-composer"),
        "usaspending.gov", "usaspending.gov", "usaspending.gov", standard=True,
        coverage_class="required-bounded",
        coverage_boundary=(
            "Approved NAICS and agency lanes using official aggregate totals "
            "plus a bounded award-detail sample for explanatory context."
        ),
        coverage_origins=("usaspending_market_evidence",),
    ),
    SourceSpec(
        "web", "Public web research", "public-web", "research-context",
        ("research-picture",), "web", "web", "web", standard=True,
        coverage_class="required-bounded",
        coverage_boundary=(
            "The approved public-web query terms with at most fifteen returned "
            "leads; this is corroborating context, not a web census."
        ),
        coverage_origins=("public_web_query",),
    ),
    SourceSpec(
        "federal_register", "Federal Register", "rulemaking",
        "program-signal", ("horizon-factory", "research-picture"),
        "federal_register", "federal_register", "federal_register",
        standard=True,
        coverage_class="required-bounded",
        coverage_boundary=(
            "The first eight approved terms, newest five Federal Register "
            "documents per term."
        ),
        coverage_origins=("federal_register_query",),
    ),
    SourceSpec(
        "news", "Federal and trade news feeds", "public-news",
        "research-context", ("research-picture",), "news", "news",
        "trade_rss", standard=True,
        coverage_class="required-bounded",
        coverage_boundary=(
            "All configured federal and trade RSS feeds, with the first forty "
            "keyword-matched items retained in feed order."
        ),
        coverage_origins=("configured_trade_feeds",),
    ),
    SourceSpec(
        "subawards", "USAspending subawards", "federal-spending",
        "teaming-evidence", ("report-composer",), "subawards", "subawards",
        "usaspending_subawards", standard=True,
        coverage_class="required-bounded",
        coverage_boundary=(
            "The first six approved NAICS lanes, three-year USAspending "
            "subaward window, and source-declared per-query/result caps."
        ),
        coverage_origins=("usaspending_subaward_query",),
    ),
    SourceSpec(
        "contract_awards", "Contract award and period-end records",
        "procurement-awards", "award-context",
        ("report-composer", "recompete-context"), "contract_awards",
        "contract_awards", "contract_awards", standard=True,
        coverage_class="required-bounded",
        coverage_boundary=(
            "Approved NAICS lanes and the configured 18-month period-end "
            "window. The keyless USAspending census covers prime contract "
            "and IDV award summaries with action dates from 2007-10-01 "
            "through the sweep as-of date, subject to explicit total-row, "
            "archive-size, and shard safety limits. It is not an all-history "
            "federal award universe."
        ),
        coverage_origins=("contract_awards_boundary",),
        official_url="https://api.usaspending.gov/docs/endpoints",
    ),
    SourceSpec(
        "dod_contracts", "DoD contract announcements", "procurement-awards",
        "program-signal", ("horizon-fact-bank", "research-picture"),
        "dod_contracts", "dod_contracts", "dod_contracts", standard=True,
        coverage_class="required-bounded",
        coverage_boundary=(
            "The current official Defense.gov contract-announcement RSS feed, "
            "with the first twenty capability matches retained."
        ),
        coverage_origins=("defense_contract_announcements",),
    ),
    SourceSpec(
        "budget_pressure", "USAspending agency budget execution",
        "federal-spending", "program-signal",
        ("horizon-factory", "research-picture"), "budget_pressure",
        "budget_pressure", "budget_pressure", standard=True,
    ),
    SourceSpec(
        "watchdogs", "GAO and Inspector General reports", "oversight",
        "program-signal", ("horizon-factory", "research-picture"),
        "watchdogs", "watchdogs", "watchdogs", standard=True,
        coverage_class="required-bounded",
        coverage_boundary=(
            "All configured GAO and Inspector General feeds, with the first "
            "twenty capability-matched reports retained."
        ),
        coverage_origins=("configured_watchdog_feeds",),
    ),
    SourceSpec(
        "congress", "Congress.gov legislation", "legislation",
        "program-signal", ("horizon-fact-bank", "research-picture"),
        "congress", "congress", "congress_gov", standard=True,
        coverage_class="required-bounded",
        coverage_boundary=(
            "The configured pages of recently updated bills in the current "
            "Congress, with the first twenty capability matches retained."
        ),
        coverage_origins=("congress_recent_bills",),
    ),
    SourceSpec(
        "kev", "CISA Known Exploited Vulnerabilities", "cybersecurity",
        "program-signal", ("horizon-factory", "research-picture"),
        "kev", "cisa_kev", "cisa_kev", standard=True,
    ),
    SourceSpec(
        "regulations", "Regulations.gov", "rulemaking", "program-signal",
        ("horizon-factory", "research-picture"), "regulations",
        "regulations_gov", "regulations_gov", standard=True,
        coverage_class="required-bounded",
        coverage_boundary=(
            "The first six approved terms, newest five Regulations.gov "
            "documents per term."
        ),
        coverage_origins=("regulations_query",),
    ),
    SourceSpec(
        "govinfo", "GovInfo legislation and reports", "legislation",
        "program-signal", ("research-picture",), "govinfo", "govinfo",
        "govinfo", standard=True,
        coverage_class="required-bounded",
        coverage_boundary=(
            "The first six approved terms across the configured GovInfo "
            "collections, newest five packages per term."
        ),
        coverage_origins=("govinfo_query",),
    ),
    SourceSpec(
        "grants", "Grants.gov", "assistance", "assistance-opportunity",
        ("horizon-factory", "source-coverage", "research-picture"),
        "grants", "grants_gov", "grants_gov", standard=True,
        coverage_class="advisory",
        coverage_boundary=(
            "Assistance-demand context selected by the approved capability "
            "vocabulary; not a procurement-opportunity census."
        ),
        official_url="https://www.grants.gov/",
    ),
    SourceSpec(
        "sbir", "SBIR.gov", "innovation-funding", "program-signal",
        ("horizon-factory", "source-coverage", "research-picture"),
        "sbir", "sbir_gov", "sbir_gov", standard=True,
        official_url="https://www.sbir.gov/topics",
    ),
    SourceSpec(
        "dsip_topics", "DoD SBIR/STTR DSIP active topics",
        "innovation-funding", "program-signal",
        ("horizon-factory", "source-coverage"),
        "dsip_topics", "dsip_topics", "dsip_topics", standard=True,
        scope_policy="defense-only",
        official_url="https://www.dodsbirsttr.mil/topics-app/",
    ),
    SourceSpec(
        "gdelt", "GDELT global news", "public-news", "research-context",
        ("research-picture",), "gdelt", "gdelt", "gdelt", standard=True,
        coverage_class="advisory",
        coverage_boundary=(
            "A bounded public-news context screen; it supplements, and never "
            "determines, federal procurement coverage."
        ),
    ),
    SourceSpec(
        "treasury", "Treasury Fiscal Data", "federal-spending",
        "program-signal", ("research-picture",), "treasury",
        "treasury_fiscal", "treasury_fiscal", standard=True,
        coverage_class="required-bounded",
        coverage_boundary=(
            "The current official Monthly Treasury Statement screen for the "
            "engagement agencies, retaining the twelve largest matched lines."
        ),
        coverage_origins=("monthly_treasury_statement",),
    ),
    SourceSpec(
        "gao", "GAO legal decisions", "procurement-law", "research-context",
        ("research-picture",), "gao", "gao_legal", "gao_legal", standard=True,
        coverage_class="required-bounded",
        coverage_boundary=(
            "The current GAO legal-decision feed, retaining the first fifteen "
            "capability-matched decisions."
        ),
        coverage_origins=("gao_legal_feed",),
    ),
    SourceSpec(
        "calc", "GSA CALC labor rates", "pricing", "market-context",
        ("research-picture",), "calc", "calc_rates", "calc_rates",
        standard=True,
        coverage_class="required-bounded",
        coverage_boundary=(
            "Official CALC rate distributions for the first five approved "
            "labor-category terms, using the first one hundred current "
            "keyword matches per term."
        ),
        coverage_origins=("calc_rate_query",),
    ),
    SourceSpec(
        "edgar", "SEC EDGAR filings", "company-filings", "market-context",
        ("research-picture",), "edgar", "sec_edgar", "sec_edgar",
        standard=True,
        coverage_class="advisory",
        coverage_boundary=(
            "Recent 10-K and 10-Q full-text context for the first four "
            "approved capability terms, capped at twelve hits per term."
        ),
    ),
    SourceSpec(
        "fedramp", "FedRAMP Marketplace", "federal-cloud",
        "market-context", ("research-picture",), "fedramp", "fedramp",
        "fedramp", standard=True,
        coverage_class="required-bounded",
        coverage_boundary=(
            "The current official FedRAMP Marketplace dataset, with the first "
            "fifteen capability-matched products retained."
        ),
        coverage_origins=("fedramp_marketplace",),
        official_url=(
            "https://github.com/FedRAMP/"
            "marketplace-fedramp-gov-data"
        ),
    ),
    SourceSpec(
        "hierarchy", "SAM.gov Federal Hierarchy", "agency-identity",
        "scope-context", ("research-picture",), "hierarchy",
        "federal_hierarchy", "federal_hierarchy", standard=True,
        coverage_class="required-bounded",
        coverage_boundary=(
            "The first engagement-scope agency and its first ten returned "
            "SAM.gov Federal Hierarchy organizations."
        ),
        coverage_origins=("sam_federal_hierarchy",),
    ),
    SourceSpec(
        "forecasts", "Agency acquisition forecasts", "procurement-forecast",
        "program-signal", ("report-composer", "horizon-fact-bank",
                           "recompete-context"),
        "forecasts", "forecast_signals", standard=True,
        coverage_class="required-bounded",
        coverage_boundary=(
            "The default official forecast contract covers the GSA "
            "Acquisition Gateway consolidated forecast; Army OSBP, NASA, "
            "State, and Education workbooks; the HHS SBCX published-forecast "
            "screen; DHS APFS; and the official HUD, SEC, NAWCWD, and NAWCAD "
            "forecast PDFs."
        ),
        coverage_origins=(
            "acquisition_gateway",
            "army_acquisition_forecast",
            "nasa_naf",
            "hhs_sbcx",
            "dhs_apfs",
            "state_forecast",
            "ed_forecast",
            "hud_forecast",
            "sec_procurement_forecast",
            "navy_nawcwd_lraf",
            "navy_nawcad_lraf",
            "va_fco",
        ),
    ),
    SourceSpec(
        "source_mesh", "Verified federal enrichment sources",
        "multi-source-enrichment", "assessment-evidence",
        ("assessment-document", "golden-press", "source-coverage"),
        "source_mesh", "source_mesh", standard=True,
        support_level="partial",
        coverage_class="required-bounded",
        coverage_boundary=(
            "The complete enabled 27-adapter official and market enrichment "
            "mesh. Each child runs against the approved client terms, firms, "
            "NAICS codes, and agency scope; a failed, partial, disabled, or "
            "inapplicable child keeps the family incomplete instead of "
            "minting a clean zero."
        ),
        coverage_origins=(
            "gsa_elibrary", "oasis_plus_holders", "fpds_atom",
            "sba_mentor_protege", "sba_sbs", "nih_reporter",
            "omb_apportionment", "abilityone_plims",
            "omb_public_budget_database", "cbca_cda_decisions",
            "ecfr_title48", "omb_sf133", "sba_procurement_scorecards",
            "hhs_oig_leie", "oversight_gov_reports", "doe_eere_exchange",
            "sam_wage_determinations", "epa_forecast", "nitaac_holders",
            "nasa_sewp_vi", "cofc_docket_rss", "treasury_sbecs",
            "doj_forecast", "gsa_it_collect",
            "sam_mirrors", "esi_reseller_catalogs", "vendor_press",
        ),
    ),
    SourceSpec(
        "buyer_map", "Incumbent buyer map", "federal-spending",
        "award-context", ("report-composer",), "buyer_map",
        "incumbent_buyer_map", standard=True,
        coverage_class="required-bounded",
        coverage_boundary=(
            "Approved incumbent terms searched against bounded USAspending "
            "award and subaward result sets under a 180-second stage budget."
        ),
        coverage_origins=("incumbent_buyer_map_query",),
    ),
    SourceSpec(
        "funded_demand", "GovInfo funded-demand evidence", "appropriations",
        "program-signal", ("research-picture",), "funded_demand",
        "funded_demand", standard=True,
        coverage_class="required-bounded",
        coverage_boundary=(
            "The first eight core capability terms across BUDGET, committee-"
            "report, and hearing collections, newest five packages per term."
        ),
        coverage_origins=("govinfo_funded_demand_query",),
    ),
    # Child/connected adapters. They keep their own provenance identity while
    # contributing through a parent lane or a later-stage enrichment surface.
    SourceSpec(
        "sam_extract", "SAM.gov daily opportunity extract", "procurement",
        "live-solicitation", ("sam.gov",), "sam.gov", "sam.gov",
        "sam_extract", parent_identity="sam.gov",
        official_url="https://sam.gov/data-services/Contract%20Opportunities/",
    ),
    SourceSpec(
        "dhs_apfs", "DHS APFS forecast", "procurement-forecast",
        "program-signal", ("forecasts",), "forecasts", "forecast_signals",
        "dhs_apfs", enabled_default=True, parent_identity="forecasts",
        official_url="https://apfs-cloud.dhs.gov/forecast/",
        coverage_active=True, agency_codes=("DHS",),
        agency_hints=("dhs", "homeland security", "cisa", "cbp", "tsa",
                      "uscis", "secret service", "fema", "ice "),
        coverage_description=(
            "Official unauthenticated DHS APFS JSON API. Every default sweep "
            "attempts the published forecast census and exposes retrieval "
            "failure or partial pagination in the family receipt."
        ),
    ),
    SourceSpec(
        "acquisition_gateway", "GSA Acquisition Gateway forecast",
        "procurement-forecast", "program-signal", ("forecasts",),
        "forecasts", "forecast_signals", "acquisition_gateway",
        enabled_default=True, parent_identity="forecasts",
        official_url="https://acquisitiongateway.gov/forecast",
        coverage_description=(
            "Government-wide consolidated forecast covering many CFO Act "
            "agencies; agency-by-agency completeness is not claimed."
        ),
    ),
    SourceSpec(
        "army_acquisition_forecast", "Army OSBP acquisition forecast",
        "procurement-forecast", "program-signal", ("forecasts",),
        "forecasts", "forecast_signals", "army_acquisition_forecast",
        enabled_default=True, parent_identity="forecasts",
        official_url=(
            "https://api.army.mil/e2/c/downloads/2026/06/18/565277f2/"
            "2026-osbp-acq-forecast-jul-dec.xlsx"
        ),
        coverage_active=True, agency_codes=("Army",),
        agency_hints=("department of the army", "u.s. army", "us army", " army "),
        coverage_description=(
            "Official Army OSBP acquisition-forecast workbook; coverage applies "
            "to Army records in that publication, not to every DoD component."
        ),
    ),
    SourceSpec(
        "nasa_naf", "NASA acquisition forecast",
        "procurement-forecast", "program-signal", ("forecasts",),
        "forecasts", "forecast_signals", "nasa_naf",
        enabled_default=True, parent_identity="forecasts",
        official_url=(
            "https://www.hq.nasa.gov/office/procurement/forecast/"
            "AcqForecastNew.xlsx"
        ),
        coverage_active=True, agency_codes=("NASA",),
        agency_hints=(
            "national aeronautics and space administration",
            " nasa ",
        ),
        coverage_description=(
            "Official NASA agency-wide acquisition-forecast workbook; "
            "Awarded and Withdrawn rows are excluded from forward signals."
        ),
    ),
    SourceSpec(
        "hhs_sbcx", "HHS SBCX opportunity forecast",
        "procurement-forecast", "program-signal", ("forecasts",),
        "forecasts", "forecast_signals", "hhs_sbcx",
        enabled_default=True, parent_identity="forecasts",
        official_url="https://osdbu.hhs.gov/industry/opportunity-forecast",
        coverage_description=(
            "Official HHS OSDBU published-forecast census: one empty-filter "
            "request returns the complete published store. The filter "
            "parameter matches title, NAICS, and division only (measured "
            "2026-08-18), so the census is the one ingest path."
        ),
    ),
    SourceSpec(
        "state_forecast", "Department of State procurement forecast",
        "procurement-forecast", "program-signal", ("forecasts",),
        "forecasts", "forecast_signals", "state_forecast",
        enabled_default=True, parent_identity="forecasts",
        official_url="https://www.state.gov/procurement-forecast/",
        coverage_active=True, agency_codes=("STATE",),
        agency_hints=(
            "department of state", "state department",
            "u.s. department of state",
        ),
        coverage_description=(
            "Official Department of State procurement-forecast workbook, "
            "rediscovered from the agency landing page on every pull."
        ),
    ),
    SourceSpec(
        "ed_forecast", "Department of Education procurement forecast",
        "procurement-forecast", "program-signal", ("forecasts",),
        "forecasts", "forecast_signals", "ed_forecast",
        enabled_default=True, parent_identity="forecasts",
        official_url=(
            "https://www.ed.gov/about/doing-business-ed/"
            "contract-opportunities/forecast-of-ed-contract-opportunities"
        ),
        coverage_active=True, agency_codes=("ED",),
        agency_hints=(
            "department of education", "dept of education",
            "education department",
        ),
        coverage_description=(
            "Official Department of Education procurement-forecast workbook, "
            "rediscovered from the agency landing page on every pull."
        ),
    ),
    SourceSpec(
        "hud_forecast", "HUD FY26-27 procurement forecast",
        "procurement-forecast", "program-signal", ("forecasts",),
        "forecasts", "forecast_signals", "hud_forecast",
        enabled_default=True, parent_identity="forecasts",
        official_url=(
            "https://www.hud.gov/sites/dfiles/SDB/documents/"
            "HUD-Forecast-FY26-27.pdf"
        ),
        coverage_active=True, agency_codes=("HUD",),
        agency_hints=("housing and urban development",),
        coverage_description=(
            "Official HUD FY2026-2027 procurement-forecast PDF. Coverage is "
            "bounded to the rows published in that artifact."
        ),
    ),
    SourceSpec(
        "sec_procurement_forecast", "SEC FY2026 procurement forecast",
        "procurement-forecast", "program-signal", ("forecasts",),
        "forecasts", "forecast_signals", "sec_procurement_forecast",
        enabled_default=True, parent_identity="forecasts",
        official_url=(
            "https://www.sec.gov/files/fy2026-sec-procurement-forecast.pdf"
        ),
        coverage_active=True, agency_codes=("SEC",),
        agency_hints=("securities and exchange commission",),
        coverage_description=(
            "Official SEC FY2026 procurement-forecast PDF. The source's "
            "opaque dollar codes are preserved and never promoted to values."
        ),
    ),
    SourceSpec(
        "navy_nawcwd_lraf", "NAWCWD FY26 long-range acquisition forecast",
        "procurement-forecast", "program-signal", ("forecasts",),
        "forecasts", "forecast_signals", "navy_nawcwd_lraf",
        enabled_default=True, parent_identity="forecasts",
        official_url=(
            "https://www.navair.navy.mil/nawcwd/sites/g/files/jejdrs601/"
            "files/document/%5Bfilename%5D/NAWCWD%20LRAF%20FY%202026.pdf"
        ),
        coverage_active=True, agency_codes=("NAWCWD",),
        agency_hints=(
            "naval air warfare center weapons division", "weapons division",
        ),
        coverage_description=(
            "Official NAWCWD FY2026 long-range acquisition-forecast PDF. "
            "Coverage applies to the Weapons Division publication only."
        ),
    ),
    SourceSpec(
        "navy_nawcad_lraf", "NAWCAD FY26 long-range acquisition forecast",
        "procurement-forecast", "program-signal", ("forecasts",),
        "forecasts", "forecast_signals", "navy_nawcad_lraf",
        enabled_default=True, parent_identity="forecasts",
        official_url=(
            "https://www.navair.navy.mil/osbp/sites/g/files/jejdrs551/"
            "files/document/%5Bfilename%5D/"
            "NAWCAD%20LRAF%20Oct25-Feb26_Final2.pdf"
        ),
        coverage_active=True, agency_codes=("NAWCAD",),
        agency_hints=(
            "naval air warfare center aircraft division", "aircraft division",
        ),
        coverage_description=(
            "Official NAWCAD FY2026 long-range acquisition-forecast PDF. "
            "Coverage applies to the Aircraft Division publication only."
        ),
    ),
    SourceSpec(
        "va_fco", "VA Forecast of Contracting Opportunities (eVP)",
        "procurement-forecast", "program-signal", ("forecasts",),
        "forecasts", "forecast_signals", "va_fco",
        enabled_default=True, parent_identity="forecasts",
        support_level="partial",
        official_url=("https://www.vendorportal.ecms.va.gov/eVP/fco/"
                      "EntireVA.aspx?Type=Complete"),
        coverage_description=(
            "VA eVP vendor-portal forecast. The search form renders "
            "anonymously but every results route is session-gated "
            "(measured 2026-08-18), so pulls report a receipted "
            "login-walled failure rather than a clean zero until a portal "
            "session or open format exists."
        ),
    ),
    _mesh_child(
        "gsa_elibrary", "GSA eLibrary MAS schedule holders",
        "contract-vehicle", "vehicle-access", ("teaming", "partner-bench"),
        "https://www.gsaelibrary.gsa.gov/",
    ),
    _mesh_child(
        "oasis_plus_holders", "OASIS+ contract holders",
        "contract-vehicle", "vehicle-access", ("teaming", "partner-bench"),
        "https://www.gsa.gov/oasisplus",
    ),
    _mesh_child(
        "fpds_atom", "FPDS-NG contract action stream",
        "federal-spending", "award-context", ("award-history", "teaming"),
        "https://www.fpds.gov/",
    ),
    _mesh_child(
        "sba_mentor_protege", "SBA active mentor-protege agreements",
        "vendor-intelligence", "target-enrichment",
        ("teaming", "partner-bench"),
        "https://www.sba.gov/document/support--active-mentor-protege-agreements",
    ),
    _mesh_child(
        "sba_sbs", "SBA small business search",
        "vendor-intelligence", "target-enrichment",
        ("teaming", "partner-bench"),
        "https://search.certifications.sba.gov/",
    ),
    _mesh_child(
        "nih_reporter", "NIH RePORTER research contracts",
        "federal-spending", "award-context", ("award-history",),
        "https://reporter.nih.gov/",
    ),
    _mesh_child(
        "omb_apportionment", "OMB public apportionments",
        "appropriations", "program-signal", ("research-picture",),
        "https://apportionment-public.max.gov/",
    ),
    _mesh_child(
        "abilityone_plims", "AbilityOne PLIMS Procurement List",
        "mandatory-source", "vehicle-access", ("golden-press",),
        "https://plims.abilityone.gov/reports/",
    ),
    _mesh_child(
        "omb_public_budget_database", "OMB Public Budget Database",
        "federal-budget", "program-signal", ("golden-press",),
        "https://www.whitehouse.gov/omb/information-resources/budget/supplemental-materials/",
    ),
    _mesh_child(
        "cbca_cda_decisions", "CBCA Contract Disputes Act decisions",
        "procurement-law", "risk-signal", ("golden-press",),
        "https://www.cbca.gov/decisions/cda-cases.html",
    ),
    _mesh_child(
        "ecfr_title48", "eCFR Title 48 acquisition regulations",
        "regulation", "requirements-context", ("golden-press",),
        "https://www.ecfr.gov/developers/documentation/api/v1",
    ),
    _mesh_child(
        "omb_sf133", "OMB SF-133 budget execution reports",
        "federal-budget", "budget-execution-context", ("golden-press",),
        "https://portal.max.gov/portal/assets/public/offm/SF133.html",
    ),
    _mesh_child(
        "sba_procurement_scorecards", "SBA procurement scorecards",
        "small-business", "agency-performance-context", ("golden-press",),
        "https://www.sba.gov/agency-scorecards",
    ),
    _mesh_child(
        "hhs_oig_leie", "HHS OIG excluded individuals and entities",
        "vendor-risk", "review-risk-signal", ("golden-press",),
        "https://oig.hhs.gov/exclusions/exclusions_list.asp",
    ),
    _mesh_child(
        "oversight_gov_reports", "Oversight.gov federal OIG reports",
        "oversight", "risk-signal", ("golden-press",),
        "https://www.oversight.gov/reports",
    ),
    _mesh_child(
        "doe_eere_exchange", "DOE EERE/CMEI eXCHANGE announcements",
        "program-funding", "program-signal", ("golden-press",),
        "https://eere-exchange.energy.gov/Default.aspx",
    ),
    _mesh_child(
        "sam_wage_determinations", "SAM.gov Wage Determinations",
        "labor-requirements", "requirements-context", ("golden-press",),
        "https://sam.gov/content/wage-determinations",
    ),
    _mesh_child(
        "epa_forecast", "EPA Acquisition Forecast Database",
        "forecast", "planning-intent", ("golden-press",),
        "https://ordspub.epa.gov/ords/forecast/f?p=122:1",
    ),
    _mesh_child(
        "nitaac_holders", "NITAAC contract-holder directory",
        "vehicle", "vehicle-access", ("golden-press",),
        "https://nitaac.nih.gov/search/contract-holders",
    ),
    _mesh_child(
        "nasa_sewp_vi", "NASA SEWP VI awardee roster",
        "vehicle", "vehicle-access", ("golden-press",),
        "https://www.sewp.nasa.gov/",
    ),
    _mesh_child(
        "cofc_docket_rss", "Court of Federal Claims recent-docket RSS",
        "procurement-law", "risk-signal", ("golden-press",),
        "https://ecf.cofc.uscourts.gov/cgi-bin/rss_outside.pl",
    ),
    _mesh_child(
        "treasury_sbecs", "Treasury Dynamic Forecast (SBECS)",
        "forecast", "planning-intent", ("golden-press",),
        "https://sbecs.treasury.gov/",
    ),
    _mesh_child(
        "doj_forecast", "DOJ Forecast of Contracting Opportunities",
        "forecast", "planning-intent", ("golden-press",),
        "https://www.justice.gov/osdbu",
    ),
    _mesh_child(
        "gsa_it_collect", "GSA IT Collect public API",
        "federal-it", "portfolio-context", ("golden-press",),
        "https://api.gsa.gov/technology/it-collect",
    ),
    # Always-swept market lanes (operator order, 2026-08-18): SAM mirrors,
    # ESI reseller catalogs, vendor press. Mirrors double as usaspending.gov
    # proxy corroboration; a walled site is a receipt, never a silent skip.
    _mesh_child(
        "sam_mirrors",
        "SAM.gov mirror category sweep (HigherGov, GovTribe, GovChime, "
        "G2Xchange, OrangeSlices AI, GDI Consulting)",
        "public-market-mirror", "notice-corroboration",
        ("golden-press", "source-coverage"),
        "https://www.highergov.com/",
    ),
    _mesh_child(
        "esi_reseller_catalogs",
        "DoD ESI reseller catalogs (esi.mil, Carahsoft, immixGroup, FCN)",
        "contract-vehicle", "vehicle-access",
        ("golden-press", "teaming"),
        "https://www.esi.mil/",
    ),
    _mesh_child(
        "vendor_press", "Vendor newsroom press roster",
        "vendor-intelligence", "vendor-signal",
        ("research-picture", "golden-press"),
        "https://www.varonis.com/press",
    ),
    SourceSpec(
        "sam_notice_detail", "SAM.gov Notice Detail and Resources",
        "procurement", "notice-enrichment",
        ("notice-dossier", "sweep"),
        connected_detail=(
            "Connected for bounded public requirements-file enrichment of "
            "actionable SAM near-matches during the refresh sweep."
        ),
        official_url="https://sam.gov/",
    ),
    SourceSpec(
        "sam_entity_exclusions", "SAM.gov Entity Management and Exclusions",
        "entity-vetting", "target-enrichment", ("target-stage",),
        adapter_name="entity_vetting",
        connected_detail=(
            "Connected for Target-stage entity vetting; not called by this "
            "refresh sweep."
        ),
        official_url="https://sam.gov/",
    ),
    SourceSpec(
        "reginfo_unified_agenda", "RegInfo Unified Agenda XML",
        "rulemaking", "program-signal",
        ("horizon-factory", "source-coverage"),
        task_key="reginfo_unified_agenda",
        result_key="reginfo_unified_agenda",
        adapter_name="reginfo_unified_agenda",
        standard=True,
        official_url="https://www.reginfo.gov/public/do/eAgendaXmlReport",
    ),
    SourceSpec(
        "darpa_opportunities", "DARPA opportunities and research notices",
        "defense-research", "program-signal",
        ("horizon-factory", "source-coverage"),
        task_key="darpa_opportunities",
        result_key="darpa_opportunities",
        adapter_name="darpa_opportunities",
        standard=True,
        scope_policy="defense-only",
        official_url="https://www.darpa.mil/work-with-us/opportunities",
    ),
    SourceSpec(
        "dod_budget_exhibits", "DoD budget exhibits",
        "defense-budget", "program-signal",
        ("horizon-factory", "source-coverage"),
        task_key="dod_budget_exhibits",
        result_key="dod_budget_exhibits",
        adapter_name="dod_budget_exhibits",
        standard=True,
        scope_policy="defense-only",
        support_level="partial",
        coverage_class="required-bounded",
        coverage_boundary=(
            "The two machine-readable DoD Comptroller JSON exhibit books: "
            "Pacific Deterrence Initiative and Counter-Drug Activities. "
            "P-1, R-1, C-1, O-1, and M-1 Excel books remain explicitly "
            "outside this adapter boundary."
        ),
        coverage_origins=(
            "pacific_deterrence_initiative",
            "counter_drug_activities",
        ),
        official_url=(
            "https://comptroller.defense.gov/Budget-Materials/"
        ),
    ),
    SourceSpec(
        "agency_program_documents", "Official agency program documents",
        "program-documents", "program-signal", ("research-subjects", "source-coverage"),
        task_key="agency_program_documents", result_key="agency_program_documents",
        adapter_name="agency_program_documents", standard=True,
        coverage_class="advisory", support_level="partial",
        coverage_boundary="Registered MDA MD30 and CBP biometric program publications and sections; not an agency publication census",
        official_url="https://comptroller.war.gov/Budget-Materials/FY2027BudgetJustification/",
    ),
    SourceSpec(
        "foreign_assistance", "ForeignAssistance.gov",
        "foreign-assistance", "program-signal",
        ("horizon-factory", "source-coverage"),
        task_key="foreign_assistance",
        result_key="foreign_assistance",
        adapter_name="foreign_assistance",
        standard=True,
        official_url="https://foreignassistance.gov/",
    ),
)


def _validated_catalog(specs: Iterable[SourceSpec]) -> tuple[SourceSpec, ...]:
    rows = tuple(specs)
    identities = [spec.identity for spec in rows]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate source identity in catalog")
    standard_tasks = [
        spec.task_key for spec in rows if spec.standard and spec.task_key]
    if len(standard_tasks) != len(set(standard_tasks)):
        raise ValueError("duplicate standard task key in source catalog")
    adapters = [spec.adapter_name for spec in rows if spec.adapter_name]
    if len(adapters) != len(set(adapters)):
        raise ValueError("duplicate adapter name in source catalog")
    known = set(identities)
    for spec in rows:
        if spec.parent_identity and spec.parent_identity not in known:
            raise ValueError(
                f"{spec.identity}: unknown parent {spec.parent_identity}")
    return rows


SOURCE_SPECS = _validated_catalog(SOURCE_SPECS)
SOURCE_BY_IDENTITY: Mapping[str, SourceSpec] = {
    spec.identity: spec for spec in SOURCE_SPECS
}
SOURCE_BY_ADAPTER: Mapping[str, SourceSpec] = {
    spec.adapter_name: spec for spec in SOURCE_SPECS if spec.adapter_name
}
STANDARD_SOURCE_SPECS: tuple[SourceSpec, ...] = tuple(
    spec for spec in SOURCE_SPECS if spec.standard
)
STANDARD_TASK_KEYS: tuple[str, ...] = tuple(
    spec.task_key for spec in STANDARD_SOURCE_SPECS if spec.task_key
)
RESULT_KEY_FOR_TASK: Mapping[str, str] = {
    spec.task_key: spec.result_key
    for spec in STANDARD_SOURCE_SPECS
    if spec.task_key and spec.result_key
}
STANDARD_SWEEP_LANES: tuple[tuple[str, str], ...] = tuple(
    (spec.task_key, spec.label)
    for spec in STANDARD_SOURCE_SPECS
    if spec.task_key
)
CONNECTED_NOT_USED: tuple[tuple[str, str, str], ...] = tuple(
    (spec.identity, spec.label, spec.connected_detail)
    for spec in SOURCE_SPECS
    if spec.connected_detail
)
FORECAST_SOURCE_NAMES: tuple[str, ...] = tuple(
    spec.adapter_name
    for spec in SOURCE_SPECS
    if spec.parent_identity == "forecasts" and spec.adapter_name
)
DEFAULT_CHILD_SOURCES_BY_PARENT: dict[str, tuple[SourceSpec, ...]] = {
    parent.identity: tuple(
        child for child in SOURCE_SPECS
        if child.parent_identity == parent.identity and child.enabled_default
    )
    for parent in STANDARD_SOURCE_SPECS
    if any(
        child.parent_identity == parent.identity and child.enabled_default
        for child in SOURCE_SPECS
    )
}


def source_spec(identity: str) -> SourceSpec:
    """Return one catalog row by provenance identity."""
    return SOURCE_BY_IDENTITY[identity]


def source_spec_for_adapter(adapter_name: str) -> SourceSpec:
    """Return one catalog row by registered adapter name."""
    return SOURCE_BY_ADAPTER[adapter_name]


def validate_standard_tasks(tasks: Mapping[str, object]) -> None:
    """Fail named before fan-out when runner callables drift from the catalog."""
    actual = tuple(tasks)
    if actual == STANDARD_TASK_KEYS:
        return
    missing = [key for key in STANDARD_TASK_KEYS if key not in tasks]
    extra = [key for key in actual if key not in STANDARD_TASK_KEYS]
    raise RuntimeError(
        "source catalog / runner task drift"
        f" (missing={missing or 'none'}, extra={extra or 'none'}, "
        f"order_expected={list(STANDARD_TASK_KEYS)}, order_actual={list(actual)})"
    )


def forecast_source_label(identity: str) -> str:
    """Public adapter label with a safe generic fallback."""
    spec = SOURCE_BY_IDENTITY.get(identity)
    return spec.label if spec else "Agency acquisition forecast"


def forecast_source_url(identity: str, fallback: str = "") -> str:
    """Official forecast landing page owned by catalog metadata."""
    spec = SOURCE_BY_IDENTITY.get(identity)
    return (spec.official_url if spec else "") or fallback
