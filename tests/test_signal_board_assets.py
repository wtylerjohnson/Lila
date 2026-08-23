"""Render-time brand-mark embedding for the Signal Board (2026-07-16).

Doctrine: seals and logos embed automatically at render from committed
local caches; a missing mark renders the existing text code plus an
INTERNAL sidecar note, never a broken image; client and partner logos load
from clients/<slug>/assets/ when present. No fetching anywhere.
"""
import base64
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))

from agents.reports import report_assets as ra  # noqa: E402
from agents.reports import signal_board as sb  # noqa: E402

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBg"
    "AAAABQABh6FO1AAAAABJRU5ErkJggg==")
_JPEG = b"\xff\xd8\xff\xe0" + b"mislabeled-jpeg"


def _clear_caches():
    ra.agency_seal.cache_clear()
    ra.client_logo.cache_clear()
    ra.company_logo.cache_clear()
    ra.partner_logo.cache_clear()


def _seals(tmp_path, monkeypatch, names=("doc", "dhs")):
    seals = tmp_path / "seals"
    seals.mkdir(exist_ok=True)
    for n in names:
        (seals / f"{n}.png").write_bytes(_PNG)
    monkeypatch.setenv("LILA_SEALS_DIR", str(seals))
    _clear_caches()


def test_component_resolves_to_department_seal(tmp_path, monkeypatch):
    """USPTO embeds the DOC seal; USCIS and CBP embed DHS; never a guess."""
    _seals(tmp_path, monkeypatch)
    for component in ("USPTO", "U.S. Patent and Trademark Office"):
        assert ra.agency_seal(component).startswith("data:image/png;base64,")
    for component in ("USCIS", "CBP",
                      "U.S. Citizenship and Immigration Services"):
        assert ra.agency_seal(component).startswith("data:image/png;base64,")


def test_mark_media_type_comes_from_bytes_not_mislabeled_extension(tmp_path):
    mark = tmp_path / "doi.png"
    mark.write_bytes(_JPEG)

    assert ra._data_uri(mark).startswith("data:image/jpeg;base64,")


def test_independent_agency_identity_uses_stable_drop_key():
    assert ra._seal_key("National Transportation Safety Board") == "ntsb"
    assert ra._seal_key("NTSB") == "ntsb"


def test_unlisted_components_share_their_department_seal_and_drop_key():
    assert ra._seal_key("FHWA") == "dot"
    assert ra._seal_key("Federal Highway Administration") == "dot"
    assert ra._seal_key("BLM") == "doi"
    assert ra._seal_key("Bureau of Land Management") == "doi"
    assert ra._seal_key("IRS · OCC") == "treas"


def test_missing_seal_is_empty_never_broken(tmp_path, monkeypatch):
    _seals(tmp_path, monkeypatch, names=())
    assert ra.agency_seal("USPTO") == ""
    assert ra.agency_seal("No Such Agency") == ""
    # renderer contract: '' -> text code badge, no has-image, no <img src="">
    badge = sb._agency_badge({"code": "DOC", "seal": ra.agency_seal("USPTO")})
    assert "has-image" not in badge and "src" not in badge


def test_client_assets_dir_wins_over_marks_cache(tmp_path, monkeypatch):
    clients = tmp_path / "clients" / "testco" / "assets"
    clients.mkdir(parents=True)
    (clients / "logo.png").write_bytes(_PNG)
    marks = tmp_path / "marks"
    marks.mkdir()
    monkeypatch.setenv("LILA_CLIENT_MARKS_DIR", str(marks))
    monkeypatch.setattr(ra, "client_assets_dir",
                        lambda name: tmp_path / "clients" / "testco" / "assets")
    _clear_caches()
    assert ra.client_logo("Testco").startswith("data:image/png;base64,")


def test_client_mark_cache_uses_canonical_slug_without_review_roster(
        tmp_path, monkeypatch):
    marks = tmp_path / "marks"
    marks.mkdir()
    (marks / "jtg_inc.png").write_bytes(_PNG)
    monkeypatch.setenv("LILA_CLIENT_MARKS_DIR", str(marks))
    monkeypatch.setattr(ra._bm, "recognize_client_key", lambda _name: None)
    monkeypatch.setattr(ra, "client_assets_dir", lambda _name: tmp_path / "none")
    _clear_caches()

    assert ra.client_logo("JTG, inc.").startswith("data:image/png;base64,")


def test_partner_logo_scopes_to_client_assets_then_falls_back(tmp_path, monkeypatch):
    partners = tmp_path / "clients" / "testco" / "assets" / "partners"
    partners.mkdir(parents=True)
    (partners / "dv_united.png").write_bytes(_PNG)
    company = tmp_path / "company"
    company.mkdir()
    monkeypatch.setenv("LILA_COMPANY_MARKS_DIR", str(company))
    monkeypatch.setattr(ra, "client_assets_dir",
                        lambda name: tmp_path / "clients" / "testco" / "assets")
    _clear_caches()
    assert ra.partner_logo("Testco", "DV United LLC").startswith("data:image")
    assert ra.partner_logo("Testco", "Unknown Partner Co") == ""


def test_client_curated_partner_asset_precedes_shared_cache(tmp_path, monkeypatch):
    partners = tmp_path / "clients" / "testco" / "assets" / "partners"
    partners.mkdir(parents=True)
    curated = b"client-curated-logo"
    (partners / "dv_united.png").write_bytes(curated)
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "dv_united.png").write_bytes(b"shared-logo")
    monkeypatch.setenv("LILA_COMPANY_MARKS_DIR", str(shared))
    monkeypatch.setattr(
        ra, "client_assets_dir",
        lambda _name: tmp_path / "clients" / "testco" / "assets",
    )
    _clear_caches()

    expected = base64.b64encode(curated).decode("ascii")
    assert ra.partner_logo("Testco", "DV United") == \
        f"data:image/png;base64,{expected}"


def test_presentation_overrides_win_and_stay_client_scoped(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from agents.reports import signal_board_presentation as presentation

    monkeypatch.setattr(presentation, "_ROOT", tmp_path)
    shared = tmp_path / "shared-company-marks"
    shared.mkdir()
    monkeypatch.setenv("LILA_COMPANY_MARKS_DIR", str(shared))
    # The historical one-pixel fixture predates upload validation, so this
    # upload uses a real presentation-sized image.
    from PIL import Image
    import io
    out = io.BytesIO()
    Image.new("RGBA", (16, 16), (1, 2, 3, 255)).save(out, format="PNG")
    presentation.store_logo_override(
        "Testco", kind="company", label="DV United LLC", raw=out.getvalue(),
        root=tmp_path,
        updated_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
    )
    _clear_caches()

    assert ra.company_logo("DV United LLC", "Testco").startswith(
        "data:image/png;base64,")
    assert ra.partner_logo("Testco", "DV United LLC").startswith(
        "data:image/png;base64,")
    assert ra.company_logo("DV United LLC", "Other Client") == ""


def test_every_dropped_identity_kind_survives_a_report_rebuild(
        tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from agents.reports import signal_board_presentation as presentation
    from test_signal_board import _full_model

    monkeypatch.setattr(presentation, "_ROOT", tmp_path)
    from PIL import Image
    import io

    def mark(color):
        out = io.BytesIO()
        Image.new("RGBA", (16, 16), color).save(out, format="PNG")
        return out.getvalue()

    for kind, label, color in (
        ("client", "ACME CORP", (1, 2, 3, 255)),
        ("agency", "USPTO", (4, 5, 6, 255)),
        ("company", "Checkmarx", (7, 8, 9, 255)),
        ("company", "ACCENTURE", (10, 11, 12, 255)),
    ):
        presentation.store_logo_override(
            "ACME CORP", kind=kind, label=label, raw=mark(color),
            root=tmp_path,
            updated_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
        )
    _clear_caches()

    model = _full_model()
    model["client_logo"] = ra.client_logo("ACME CORP")
    for row in model["coverage"]:
        row["seal"] = ra.agency_seal("USPTO", "ACME CORP")
    for row in model["best_fit"]:
        row["seal"] = ra.agency_seal("USPTO", "ACME CORP")
    model["competitors"][0]["logo"] = ra.company_logo(
        "Checkmarx", "ACME CORP")
    model["horizon"][0]["organization_marks"] = [{
        "kind": "agency", "label": "USPTO", "display": "USPTO",
        "logo": ra.agency_seal("USPTO", "ACME CORP"),
    }, {
        "kind": "client", "label": "ACME CORP", "display": "ACME",
        "logo": ra.client_logo("ACME CORP"),
    }, {
        "kind": "company", "label": "Checkmarx", "display": "Checkmarx",
        "logo": ra.company_logo("Checkmarx", "ACME CORP"),
    }]
    model["teaming"][0]["client_logo"] = ra.client_logo("ACME CORP")
    model["teaming"][0]["partner_logo"] = ra.partner_logo(
        "ACME CORP", "ACCENTURE")

    html = sb.render_signal_board(model)
    assert html.count('data-brand-key="client:acme_corp"') >= 2
    assert html.count('data-brand-key="agency:doc"') >= 3
    assert 'data-brand-key="company:checkmarx"' in html
    assert 'data-brand-key="company:accenture"' in html
    for raw in (
        model["client_logo"], model["coverage"][0]["seal"],
        *(mark["logo"] for mark in model["horizon"][0]["organization_marks"]),
        model["competitors"][0]["logo"],
        model["teaming"][0]["partner_logo"],
    ):
        assert raw and raw in html


def test_missing_seal_writes_internal_note_not_client_copy(tmp_path, monkeypatch):
    """The note rides model['internal_notes'] for the INTERNAL sidecar; the
    client render carries the text code and no note vocabulary."""
    _seals(tmp_path, monkeypatch, names=())
    from agents.reports.document import build_document
    from test_assessment_document import _searches
    doc = build_document("Testco", searches=_searches(2), qualify=None,
                         as_of=date(2026, 7, 15))
    model = sb.build_model(doc, report_date="15 JUL 2026")
    assert model["internal_notes"], "missing seals must be noted"
    assert all("data/reference/seals" in n for n in model["internal_notes"])
    html = sb.render_signal_board(model)
    assert "internal_notes" not in html
    assert "seal asset" not in html


def test_template_carries_zero_em_dashes_and_stays_white_label():
    tpl = sb._load_template()
    assert "—" not in tpl, "golden manifest requires zero em dashes"
    from agents.reports.lint import lint_whitelabel
    assert lint_whitelabel(tpl).ok
