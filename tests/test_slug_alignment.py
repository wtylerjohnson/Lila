"""One client identity, one slug (defect measured 2026-08-19).

"JTG, inc." minted clients/jtg__inc at intake while the sweep looked for
clients/jtg_inc: two slugifiers disagreed on consecutive separators. The
pin: the Control Room and the capability layer produce the same slug for
every name shape that has burned or could.
"""

from tools.capability import _slug
from ui.server import _slugify


def test_slugifiers_agree_on_separator_runs():
    for name, expected in (
        ("JTG, inc.", "jtg_inc"),
        ("Red Hat", "red_hat"),
        ("Arista Networks", "arista_networks"),
        ("Osprey Flight Solutions", "osprey_flight_solutions"),
        ("Acme, Federal - West", "acme_federal_west"),
        ("mark43", "mark43"),
    ):
        assert _slugify(name) == expected
        assert _slug(name) == expected


def test_review_layer_slugs_agree_too():
    from agents.review import _artifact_slug, _slug as review_slug
    for name, expected in (
        ("JTG, inc.", "jtg_inc"),
        ("Red Hat", "red_hat"),
        ("Acme, Federal - West", "acme_federal_west"),
    ):
        assert _artifact_slug(name) == expected
        assert review_slug(name) == expected


def test_workstation_layer_slug_agrees():
    from agents.workstations import _slug as ws_slug
    assert ws_slug("JTG, inc.") == "jtg_inc"
    assert ws_slug("Red Hat") == "red_hat"
