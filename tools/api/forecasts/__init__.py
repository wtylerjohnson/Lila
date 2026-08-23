"""Agency procurement-forecast adapters — the pre-RFP signals layer.

Forecasts are statutorily required projections of anticipated contract actions
(P.L. 100-656): agency-stated INTENT, not live opportunities, revised or
cancelled routinely. Everything in this subpackage preserves that distinction:

  - Adapters expose ForecastRecord through forecasts(). A few verified agency
    workbook/PDF parsers use RawOpportunity as an internal normalization
    surface, but they are SourceKind.ENRICHMENT and therefore cannot enter the
    live-opportunity discovery fan-out.
  - Matching (match_forecasts) reuses the same NAICS/set-aside/keyword gates as
    the SAM path but routes hits into the SIGNALS flow only.
  - Rendering happens exclusively in the capture brief's Early Signals section;
    the lint gate fails any build where a forecast renders outside it.

Adding an agency = one file here + one @register_source decorator.
"""

import os

from tools.api.forecasts import acquisition_gateway  # noqa: F401 — registration
from tools.api.forecasts import army_acquisition_forecast  # noqa: F401 — registration
from tools.api.forecasts import dhs_apfs  # noqa: F401 — registration side effect
from tools.api.forecasts import hhs_sbcx  # noqa: F401 — registration
from tools.api.forecasts import nasa_naf  # noqa: F401 — registration
from tools.api.forecasts import va_fco  # noqa: F401 — registration (A8)
from tools.api.forecasts.coverage import (  # noqa: F401
    coverage_table, gaps_for, record_pull,
)
from tools.api.forecasts.matching import (  # noqa: F401
    bucket_forecasts, match_forecasts, screen_line,
)
from tools.api.forecasts.snapshots import (  # noqa: F401
    diff_snapshots, load_latest_snapshot, save_snapshot,
)
from tools.api.forecasts.store import (  # noqa: F401
    record_payload, stated_value_bounds, upsert as store_upsert,
)
from tools.api.source_catalog import FORECAST_SOURCE_NAMES  # noqa: F401


def forecast_sources():
    """Every registered forecast adapter, enabled or not — the sweep loop
    iterates these so adding an agency never touches run_searches."""
    from tools.api import REGISTRY
    return [REGISTRY.get(n) for n in FORECAST_SOURCE_NAMES if REGISTRY.get(n)]


def strict_offline() -> bool:
    """Return whether this run forbids every live forecast retrieval."""

    return os.environ.get("LILA_SUITE_OFFLINE", "").strip().casefold() in {
        "1", "true", "yes", "on",
    }


def source_is_offline_safe(source) -> bool:
    """Fixtures and local adapters may explicitly opt into offline execution."""

    return bool(getattr(source, "offline_safe", False))
