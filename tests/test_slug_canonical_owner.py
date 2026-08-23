"""One canonical slug owner (consolidation, 2026-08-19).

tools.slug.client_slug is the only sanctioned client-identity slug.
Pins here: every consolidated site delegates (same outputs on the burned
name shapes), the legacy shapes remain importable for read fallbacks
only, and the FROZEN AUDIT: every client name existing on disk on
consolidation day was verified family-identical (per-character ==
run-collapse == hyphen modulo separator), so delegation could not orphan
any existing artifact. Names onboarded after the alignment always mint
canonical.
"""

from __future__ import annotations

import pytest

from tools.slug import client_slug, legacy_client_slug, legacy_hyphen_client_id

BURNED = [
    ("JTG, inc.", "jtg_inc"),
    ("Booz Allen, Inc.", "booz_allen_inc"),
    ("Acme, Federal - West", "acme_federal_west"),
    ("Red Hat", "red_hat"),
    ("Arista Networks", "arista_networks"),
    ("mark43", "mark43"),
]

#: Every client identity present on disk at consolidation (harvested
#: 2026-08-19 from data/review packets in the primary clone). Each was
#: measured family-identical; a new divergent-shape client is SAFE (it
#: mints canonical everywhere), but this list must never regress.
FROZEN_AUDIT_NAMES = [
    "Insignary", "NETSCOUT", "NetApp", "Osprey Flight Solutions",
    "Recorded Future", "Red Hat", "Riverbed", "Thinklogical", "Varonis",
    "apexanalytix", "mark43",
]


def test_canonical_and_legacy_shapes():
    for name, expected in BURNED:
        assert client_slug(name) == expected
    assert legacy_client_slug("JTG, inc.") == "jtg__inc"
    assert legacy_hyphen_client_id("JTG, inc.") == "jtg-inc"
    # idempotence: a slug is a fixed point of the canonical function
    for name, expected in BURNED:
        assert client_slug(expected) == expected


def test_frozen_audit_names_are_family_identical():
    for name in FROZEN_AUDIT_NAMES:
        canonical = client_slug(name)
        assert canonical == legacy_client_slug(name), name
        assert canonical == legacy_hyphen_client_id(name).replace("-", "_"), name


@pytest.mark.parametrize("get_fn", [
    lambda: __import__("tools.capability", fromlist=["_slug"])._slug,
    lambda: __import__("agents.assess.ledger", fromlist=["_slug"])._slug,
    lambda: __import__("agents.assess.live_review", fromlist=["_slug"])._slug,
    lambda: __import__("agents.change_digest", fromlist=["_slug"])._slug,
    lambda: __import__("agents.press_snapshot",
                       fromlist=["client_slug"]).client_slug,
    lambda: __import__("agents.reports.document", fromlist=["_slug"])._slug,
    lambda: __import__("agents.reports.facts", fromlist=["_slug"])._slug,
    lambda: __import__("agents.reports.horizon", fromlist=["_slug"])._slug,
    lambda: __import__("agents.review", fromlist=["_artifact_slug"])._artifact_slug,
    lambda: __import__("agents.review", fromlist=["_slug"])._slug,
    lambda: __import__("agents.workstations", fromlist=["_slug"])._slug,
    lambda: __import__("tools.api.recompete", fromlist=["_slug"])._slug,
    lambda: __import__("tools.brand_marks", fromlist=["_slugify"])._slugify,
    lambda: __import__("tools.crm.apollo_handoff", fromlist=["_slug"])._slug,
    lambda: __import__("ui.server", fromlist=["_slugify"])._slugify,
    lambda: __import__("run_agency_report", fromlist=["_slug"])._slug,
    lambda: __import__("run_candidates", fromlist=["_slug"])._slug,
    lambda: __import__("run_horizon", fromlist=["_slug"])._slug,
    lambda: __import__("run_market_refresh", fromlist=["_slug"])._slug,
    lambda: __import__("run_signal_board", fromlist=["_slug"])._slug,
    lambda: __import__("run_views", fromlist=["_slug"])._slug,
    lambda: __import__("run_refresh_press", fromlist=["_slug"])._slug,
])
def test_every_site_delegates_to_the_owner(get_fn):
    fn = get_fn()
    for name, expected in BURNED:
        assert fn(name) == expected


def test_candidate_review_mints_are_unified():
    # One client, one candidate-review directory: the watch and the press
    # derive the same canonical id (legacy hyphen dirs stay readable
    # through the watch's documented fallback).
    from run_candidate_review import _client_id
    from run_candidate_review_watch import _candidate_client_id
    for name, expected in BURNED:
        assert _candidate_client_id(name) == expected
        assert _client_id(name) == expected
