"""API source package.

Importing this package registers all bundled adapters into REGISTRY. To add a new
source, create a module here with an @register_source-decorated DataSource subclass
and import it below — nothing else in the system needs to change.
"""

from __future__ import annotations

from tools.api.base import (  # noqa: F401
    REGISTRY,
    DataSource,
    SourceKind,
    SourceQuery,
    SourceRegistry,
    discover,
    register_source,
)
from tools.api.provenance import (  # noqa: F401
    ProvenanceAttempt,
    ProvenanceEnvelope,
    make_provenance_envelope,
    provenance_from_payload,
)
from tools.api.source_catalog import SourceSpec  # noqa: F401

# Import adapters for their registration side effects.
from tools.api import (  # noqa: E402,F401
    abilityone_plims,
    calc_rates,
    cbca_cda_decisions,
    cisa_kev,
    congress_gov,
    contract_awards,
    cofc_docket_rss,
    darpa_opportunities,
    dod_budget_exhibits,
    agency_program_documents,
    dod_contracts,
    doe_eere_exchange,
    doj_forecast,
    dsip_topics,
    ed_forecast,
    ecfr_title48,
    epa_forecast,
    entity_vetting,
    esi_catalogs,
    federal_hierarchy,
    federal_register,
    foreign_assistance,
    fedramp,
    fpds_atom,
    gao_legal,
    gdelt,
    govinfo,
    grants_gov,
    gsa_elibrary,
    gsa_it_collect,
    hhs_oig_leie,
    hud_forecast,
    nasa_sewp_vi,
    nih_reporter,
    nitaac_holders,
    navy_nawcad_lraf,
    navy_nawcwd_lraf,
    oasis_plus_holders,
    omb_apportionment,
    omb_public_budget,
    omb_sf133,
    oversight_gov_reports,
    reginfo_unified_agenda,
    regulations_gov,
    sam_extract,
    sam_gov,
    sam_mirrors,
    sam_wage_determinations,
    sba_mentor_protege,
    sba_procurement_scorecards,
    sba_sbs,
    sbir_gov,
    sec_procurement_forecast,
    sec_edgar,
    state_forecast,
    trade_rss,
    treasury_sbecs,
    treasury_fiscal,
    usaspending,
    usaspending_budget,
    usaspending_subawards,
    vendor_press,
    watchdog_rss,
    web_search,
)
from tools.api import forecasts  # noqa: E402,F401 — forecast adapters (signals layer)

__all__ = [
    "REGISTRY",
    "DataSource",
    "SourceKind",
    "SourceQuery",
    "SourceRegistry",
    "discover",
    "register_source",
    "ProvenanceAttempt",
    "ProvenanceEnvelope",
    "SourceSpec",
    "make_provenance_envelope",
    "provenance_from_payload",
]
