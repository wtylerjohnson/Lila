"""Golden conformance test for an internal compatibility format.

The Signal Board remains internally locked and deterministic (no LLM composes
the artifact), but it no longer defines LILA's external product. This test
pins its compatibility behavior: no cross-client leakage, structured/escaped
output, and an EMPTY model fails conformance. See
docs/opportunity_signals_format.md.
"""
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.reports import signal_board as sb


def _full_model() -> dict:
    cells = [{"label": lbl, "value": "x", "small": "OBLIGATED"}
             for lbl in ("Contract scale", "Current ends", "Fit", "Route")]
    opp = {"rank": "01", "code": "DOC", "agency_name": "Commerce",
           "title": "USPTO × Accenture", "url": "https://sam.gov/opp/a",
           "account": "COMMERCE", "seal": None,
           "evidence": [{"url": "https://www.usaspending.gov/award/Y", "label": "1333BJ"}],
           "cells": cells}
    return {
        "client_name": "ACME CORP",
        "client_logo": "data:image/svg+xml;base64,PHN2Zy8+",
        "report_date": "15 JUL 2026",
        "hero_context": "Screened primary federal records for ACME.",
        "scale_total": "$289.5M", "scale_counts": "3 corridors · 4 records",
        "scale": {"rows": [{"label": "USPTO × Accenture",
                            "url": "https://www.usaspending.gov/award/Y",
                            "naics": "541512", "amount": "$71,230,136.71"}],
                  "total": "$71,230,136.71"},
        "chips": [{"label": "BINARY SCA", "tone": "hot"}, {"label": "REACHABILITY"}],
        "news": [{"head": "USPTO", "body": "$71.23M", "url": "https://x.gov/1"}],
        "signal_cards": [{"label": f"Card {i}", "value": str(i), "sub": "s", "detail": "d"}
                         for i in range(4)],
        "coverage": [{"dept": "COMMERCE", "components": "USPTO", "seal": None}],
        "best_fit": [dict(opp), dict(opp, rank="02")],
        "competitors": [{"label": "USCIS · ACTIVE", "title": "Checkmarx",
                         "url": "https://www.usaspending.gov/award/Z", "money": "$2M",
                         "small": "OBLIGATED", "wedge": "Lane"}],
        "teaming": [{"client": "ACME", "partner": "ACCENTURE", "target": "USPTO",
                     "angle": "AVP Alliances", "proof": "541519 reseller"}],
        "horizon": [{"hot": True, "label": "DARPA · TRANSITION",
                     "agency_name": "DARPA",
                     "organization_marks": [{
                         "kind": "agency", "label": "DARPA",
                         "display": "DARPA", "logo": "",
                     }],
                     "title": "E-BOSS", "url": "https://www.darpa.mil/x",
                     "value": "$3.86M", "small": "OBLIGATED"}],
        "pocs": [{"opp": "USCIS", "opp_url": "https://sam.gov/opp/b", "name": "Jane Doe",
                  "role": "Contract Specialist", "deadline": "24 SEP", "signal": "SBOM"}],
        "evidence": [{"url": "https://www.usaspending.gov/award/Y", "label": "USPTO award"}],
        "sequence": "Lead with the evidenced route.",
        # per-figure provenance (2026-07-16): the data-current line computes
        # from these; min is 14 JUL, one day inside the document date
        "figures": [{"retrieved_at": "2026-07-15T08:00:00+00:00"},
                    {"retrieved_at": "2026-07-14T09:30:00+00:00"}],
    }


def test_template_exposes_every_declared_token():
    tpl = sb._load_template()
    for tok in sb.TOKENS:
        assert "{{" + tok + "}}" in tpl, f"template is missing token {tok}"


def test_render_fills_every_token_and_conforms():
    html = sb.render_signal_board(_full_model())
    assert re.findall(r"\{\{\w+\}\}", html) == []
    ok, violations = sb.lint_signal_board(html)
    assert ok, violations


def test_public_name_is_exact_and_identity_surfaces_are_logo_targets():
    html = sb.render_signal_board(_full_model())

    assert "<title>ACME CORP · Federal Opportunity Pre-Assessment</title>" in html
    assert '<h1 id="signalTitle">Federal Opportunity<br>Pre-Assessment</h1>' in html
    assert "ACME CORP Federal Opportunity Pre-Assessment:" in html
    assert "Federal Opportunity Signals" not in html
    assert '<h2 id="teamingTitle">Candidate teaming opportunities</h2>' in html
    assert '<h2 id="teamingTitle">Teaming opportunities</h2>' not in html
    assert '<h2 id="horizonTitle">Forward timing signals</h2>' in html
    assert 'data-brand-kind="client" data-brand-key="client:acme_corp"' in html
    assert html.count('data-brand-key="agency:doc"') >= 3
    assert 'data-brand-key="company:checkmarx"' in html
    assert html.count('data-brand-key="client:acme_corp"') >= 2
    assert 'data-brand-key="company:accenture"' in html
    assert 'class="sb-horizon-mark" data-brand-kind="agency"' in html
    assert 'data-brand-label="DARPA"' in html
    assert "<script" not in html


def test_section_03_switches_to_distinct_acquisition_path_research():
    model = _full_model()
    model["teaming"] = []
    model["acquisition_pathways"] = [{
        "label": "USAF · PUBLISHED NOTICE",
        "title": "USAF acquisition route",
        "agency_name": "Department of the Air Force",
        "requirement": (
            "Commercial Solutions Opening for data curation & validation"),
        "pathway": (
            "ACQUISITION METHOD Commercial Solutions Opening · "
            "SOLICITATION FA701426SCS01"),
        "access": "SET-ASIDE Total Small Business",
        "action": "RESOLVE NEXT · vehicle access and proposal milestone.",
        "url": "https://sam.gov/opp/route",
        "citation_label": "SAM notice FA701426SCS01",
        "procurement_facts": [{
            "label": "NAICS", "value": "541715", "source_field": "NAICS",
        }],
        "missing_procurement_facts": ["PSC"],
    }]

    html = sb.render_signal_board(model)

    assert '<a href="#teaming">Acquisition paths</a>' in html
    assert '<h2 id="teamingTitle">Federal acquisition pathways</h2>' in html
    assert ("SOURCE-STATED ROUTES · ACCESS CONDITIONS · "
            "VALIDATION QUEUE" in html)
    assert "Candidate teaming opportunities" not in html
    assert "Commercial Solutions Opening for data curation &amp; validation" \
        in html
    assert "Source-stated buying path" in html
    assert "Capture research action" in html
    ok, violations = sb.lint_signal_board(html)
    assert ok, violations


def test_procurement_facts_and_forward_decision_evidence_are_visible():
    facts = [{
        "label": "NAICS", "value": "541512",
        "source_field": "NAICS",
    }, {
        "label": "Place", "value": "Denver & Boulder",
        "source_field": "place of performance",
    }]
    missing = ["PSC", "Set-aside"]

    opportunity = sb._opp({
        "rank": "01", "code": "DOC", "agency_name": "Commerce",
        "title": "Cited route", "url": "https://sam.gov/opp/a",
        "account": "COMMERCE", "evidence": [], "cells": [],
        "procurement_facts": facts,
        "missing_procurement_facts": missing,
    })
    competitor = sb._competitor({
        "title": "Prime", "procurement_facts": facts,
        "missing_procurement_facts": missing,
    })
    teaming = sb._team({
        "client": "ACME", "partner": "Prime", "target": "Route",
        "procurement_facts": facts,
        "missing_procurement_facts": missing,
    })
    horizon = sb._horizon({
        "title": "Forecast route", "decision": "ATTACK",
        "source_citation": "Agency forecast · F-100",
        "timing_basis": {
            "source_value": "FY 2027 Q2", "precision": "quarter",
            "sort_date": "2027-01-01",
        },
        "procurement_facts": facts,
        "missing_procurement_facts": missing,
    })

    for card in (opportunity, competitor, teaming, horizon):
        assert 'class="sb-procurement-panel"' in card
        assert "Procurement record" in card
        assert "541512" in card
        assert "Denver &amp; Boulder" in card
        assert "place of performance" in card
        assert "Not stated in cited record" in card
        assert "PSC · Set-aside" in card
    assert 'class="sb-horizon-decision is-attack">ATTACK</div>' in horizon
    assert "Agency forecast · F-100" in horizon
    assert "Source timing · FY 2027 Q2 · QUARTER" in horizon
    assert "2027-01-01" not in horizon


def test_forward_decision_band_states_traceability_boundary():
    model = _full_model()
    model["horizon"][0]["decision"] = "QUALIFY"
    html = sb.render_signal_board(model)

    assert '<a href="#horizon">Forward timing</a>' in html
    assert '<h2 id="horizonTitle">Forward timing signals</h2>' in html
    assert "1 CITED FORWARD EVENT · 1 QUALIFY · NOT QUALIFIED PIPELINE" in html
    assert ("every market figure maps to a cited federal record; source "
            "connection scope is disclosed") in html
    assert "not qualified pipeline" in html.lower()
    assert "—" not in sb._load_template()


def test_header_companion_text_is_a_validated_presentation_slot():
    model = _full_model()
    model["header_companion_text"] = "FEDERAL CAPTURE PRIORITIES"

    html = sb.render_signal_board(model)

    assert ('<span class="sb-brand-sub">FEDERAL CAPTURE PRIORITIES</span>'
            in html)
    assert html == sb.render_signal_board(model)

    model["header_companion_text"] = "<script>alert(1)</script>"
    with pytest.raises(ValueError, match="markup"):
        sb.render_signal_board(model)


def test_build_model_reads_client_scoped_header_companion_manifest(
        tmp_path, monkeypatch):
    from agents.reports import report_assets
    from agents.reports import signal_board_presentation as presentation

    monkeypatch.setattr(presentation, "_ROOT", tmp_path)
    presentation.store_header_companion_text(
        "Testco", "FEDERAL CAPTURE PRIORITIES", root=tmp_path)
    monkeypatch.setattr(
        "tools.capability.client_display_name", lambda name: name)
    monkeypatch.setattr(report_assets, "client_logo", lambda _name: "")

    model = sb.build_model(
        SimpleNamespace(client_name="Testco", counts=lambda: {}),
        report_date="19 JUL 2026",
        figures=[{"retrieved_at": "2026-07-19T00:00:00+00:00"}],
    )

    assert model["header_companion_text"] == "FEDERAL CAPTURE PRIORITIES"


def test_locked_reference_targets_every_entity_mark_and_locks_publisher_mark():
    reference = (
        Path(sb.__file__).parents[2]
        / "docs/reference/federal_opportunity_signals.reference.html"
    ).read_text(encoding="utf-8")
    for css_class in (
        "sb-brand", "sb-agency-mark", "sb-agency-code",
        "sb-competitor", "sb-company", "sb-horizon-mark",
    ):
        tags = re.findall(
            rf'<(?:div|article)\b[^>]*class="[^"]*'
            rf'(?<![\w-]){re.escape(css_class)}(?![\w-])[^"]*"[^>]*>',
            reference,
        )
        assert tags, f"locked reference has no {css_class} identity shapes"
        assert all("data-brand-kind=" in tag and "data-brand-key=" in tag
                   for tag in tags), css_class
    publisher = re.search(
        r'<div class="sb-utility-meta has-image"[^>]*>'
        r'<img class="sb-header-logo" alt="GTM logo"[^>]*>',
        reference,
    )
    assert publisher
    assert "data-brand-kind=" not in publisher.group(0)
    assert "data-brand-key=" not in publisher.group(0)
    assert 'class="sb-watch-guide" role="note"' in reference
    assert 'class="sb-horizon-work"' in reference
    assert "CITED TIMING WATCHLIST · NOT QUALIFIED PIPELINE" in reference
    assert "How to use this" in reference


def test_competitor_card_keeps_name_when_a_logo_is_available():
    model = _full_model()
    model["competitors"][0]["logo"] = "data:image/png;base64,AAA="

    html = sb.render_signal_board(model)

    assert 'class="sb-competitor has-logo"' in html
    assert 'class="sb-competitor-logo" alt="Checkmarx logo"' in html
    assert ">Checkmarx</a>" in html

    ok, violations = sb.lint_signal_board(html)
    assert ok, violations


def test_horizon_card_keeps_distinct_agency_client_and_company_mark_targets():
    model = _full_model()
    model["horizon"][0]["organization_marks"] = [{
        "kind": "agency", "label": "DARPA", "display": "DARPA",
        "logo": "data:image/png;base64,AAA=",
    }, {
        "kind": "client", "label": "ACME CORP", "display": "ACME",
        "logo": "",
    }, {
        "kind": "company", "label": "Galois, Inc.", "display": "Galois",
        "logo": "",
    }]

    html = sb.render_signal_board(model)

    assert '<article class="sb-horizon-card hot">' in html
    assert ('class="sb-horizon-mark has-logo" data-brand-kind="agency" '
            'data-brand-key="agency:dod"') in html
    assert ('class="sb-horizon-logo" alt="DARPA logo" '
            'src="data:image/png;base64,AAA="') in html
    assert 'data-brand-key="client:acme_corp"' in html
    assert 'data-brand-key="company:galois"' in html
    assert '<article class="sb-horizon-card hot" data-brand-' not in html
    assert ">DARPA · TRANSITION</div>" in html
    assert ">E-BOSS</a>" in html


def test_award_period_horizon_explains_dates_and_next_action():
    model = _full_model()
    model["horizon"] = [{
        "label": "IRS · AWARD WINDOW",
        "title": "Riverbed via RedSky",
        "value": "31 JUL 2026",
        "small": "ACTIVE AWARD · PERIOD OF PERFORMANCE END",
        "job_context": {
            "kind": "published-description",
            "text": ("RIVERBED NETWORK MONITORING HARDWARE AND SOFTWARE "
                     "MAINTENANCE, AND SUPPORT"),
        },
        "organization_marks": [
            {"kind": "agency", "label": "Treasury", "display": "IRS"},
            {"kind": "client", "label": "Riverbed", "display": "Riverbed"},
            {"kind": "company", "label": "RedSky", "display": "RedSky"},
        ],
        "machine_evidence": {
            "source_kind": "usaspending-award",
            "quote": "CLIPPED INTERNAL QUOTE",
        },
    }, {
        "label": "BLM · AWARD WINDOW",
        "title": "Riverbed via Swish Data",
        "value": "30 SEP 2026",
        "small": "ACTIVE AWARD · PERIOD OF PERFORMANCE END",
        "job_context": {
            "kind": "published-description",
            "text": "FY25 RIVERBED STEELHEAD, TOOLS & MANAGER",
        },
        "organization_marks": [
            {"kind": "agency", "label": "Interior", "display": "BLM"},
            {"kind": "client", "label": "Riverbed", "display": "Riverbed"},
            {"kind": "company", "label": "Swish", "display": "Swish"},
        ],
        "machine_evidence": {
            "source_kind": "usaspending-award",
            "quote": "FY25 RIVERBED STEELHEAD, TOOLS & MANAGER",
        },
    }]

    html = sb.render_signal_board(model)

    assert "2 CITED FORWARD EVENTS · NOT QUALIFIED PIPELINE" in html
    assert ("Each card pairs a cited federal timing fact with the procurement "
            "fields stated by its source.") in html
    assert "2 cited forward events form this decision queue." in html
    assert ("All 2 events use cited active-award period ends as decision "
            "clocks, not separate solicitation records.") in html
    assert ("The source-stated sequence runs from 31 JUL 2026 through 30 SEP 2026, "
            "beginning with IRS.") in html
    assert "ATTACK marks an entry route" in html
    assert "IRS · AWARD PERIOD END" in html
    assert "IRS · AWARD WINDOW" not in html
    assert "THIS IS A DEFINITIVE CONTRACT TO ACQUIRE" not in html
    assert "CLIPPED INTERNAL QUOTE" not in html
    assert ("RIVERBED NETWORK MONITORING HARDWARE AND SOFTWARE "
            "MAINTENANCE, AND SUPPORT") in html
    assert "FY25 RIVERBED STEELHEAD, TOOLS &amp; MANAGER" in html
    assert html.count('class="sb-horizon-work"') == 2
    assert html.count("<b>Cited work</b>") == 2
    assert "TRANSITION · OPEN CALLS · RECOMPETE · CHANNEL" not in html


def test_product_only_horizon_description_names_the_source_limit():
    model = _full_model()
    model["horizon"] = [{
        "label": "FBI · AWARD WINDOW",
        "title": "Riverbed via Swish Data",
        "value": "24 SEP 2026",
        "small": "ACTIVE AWARD · PERIOD OF PERFORMANCE END",
        "job_context": {
            "kind": "limited-description",
            "text": ("RIVERBED · fuller work detail is not stated in the "
                     "cited record."),
        },
        "organization_marks": [{
            "kind": "client", "label": "Riverbed", "display": "Riverbed",
        }],
        "machine_evidence": {
            "source_kind": "usaspending-award", "quote": "RIVERBED",
        },
    }]

    html = sb.render_signal_board(model)

    assert ("RIVERBED · fuller work detail is not stated in the cited record."
            in html)
    assert "network monitoring" not in html.lower()


def test_award_horizon_never_infers_relationships_from_mark_kinds():
    model = _full_model()
    base = {
        "small": "ACTIVE AWARD · PERIOD OF PERFORMANCE END",
        "machine_evidence": {"source_kind": "usaspending-award"},
    }
    model["horizon"] = [dict(
        base,
        label="NTSB · AWARD WINDOW",
        title="Mark43",
        value="26 SEP 2026",
        organization_marks=[
            {"kind": "agency", "label": "NTSB", "display": "NTSB"},
            {"kind": "client", "label": "Mark43", "display": "Mark43"},
        ],
    )] + [dict(
        base,
        label=f"{agency} · AWARD WINDOW",
        title=company,
        value=value,
        organization_marks=[
            {"kind": "agency", "label": agency, "display": agency},
            {"kind": "company", "label": company, "display": company},
        ],
    ) for agency, company, value in (
        ("USCG", "Axon", "29 SEP 2026"),
        ("NAVY", "CentralSquare", "09 DEC 2026"),
        ("FBI", "OpenFox", "31 MAR 2027"),
    )]

    html = sb.render_signal_board(model)

    assert "4 cited forward events form this decision queue." in html
    guide = re.search(
        r'<div class="sb-watch-guide"[^>]*>.*?<p>(.*?)</p></div>', html)
    assert guide is not None
    assert "reseller" not in guide.group(1).lower()
    assert "competitive window" not in guide.group(1).lower()
    assert ("The source-stated sequence runs from 26 SEP 2026 through 31 MAR 2027, "
            "beginning with NTSB.") in html


def test_single_award_period_horizon_uses_singular_grammar():
    model = _full_model()
    model["horizon"] = [{
        "label": "NTSB · AWARD WINDOW",
        "title": "Mark43",
        "value": "26 SEP 2026",
        "small": "ACTIVE AWARD · PERIOD OF PERFORMANCE END",
        "machine_evidence": {"source_kind": "usaspending-award"},
    }]

    html = sb.render_signal_board(model)

    assert "1 cited forward event forms this decision queue." in html
    assert ("This event uses a cited active-award period end as its decision "
            "clock, not a separate solicitation record.") in html


def test_mixed_horizon_uses_general_cited_timing_contract():
    html = sb.render_signal_board(_full_model())

    assert ("obligations show what agencies bought, active awards show "
            "funded work now, and forward timing signals show cited upcoming "
            "clocks") in html
    assert "1 CITED FORWARD EVENT · NOT QUALIFIED PIPELINE" in html
    assert "Each card pairs a cited federal timing fact" in html
    assert "1 cited forward event forms this decision queue." in html
    assert ("The queue is drawn from cited forecast, recompete, expiry, or "
            "program records rather than active-award period ends.") in html
    assert 'class="sb-watch-guide" role="note"' in html


def test_mixed_horizon_guide_uses_singular_remaining_event_grammar():
    model = _full_model()
    model["horizon"].append({
        "label": "NTSB · AWARD WINDOW",
        "title": "Mark43",
        "value": "26 SEP 2026",
        "small": "ACTIVE AWARD · PERIOD OF PERFORMANCE END",
        "machine_evidence": {"source_kind": "usaspending-award"},
    })

    html = sb.render_signal_board(model)

    assert ("1 of 2 cited events use active-award period ends; the remaining "
            "1 comes from separate cited forecast, recompete, expiry, or "
            "program records.") in html


def test_horizon_requires_the_complete_award_period_contract():
    model = _full_model()
    source_only = dict(model["horizon"][0],
                       machine_evidence={"source_kind": "usaspending-award"})
    label_only = dict(model["horizon"][0],
                      label="DARPA · AWARD WINDOW")
    malformed = dict(
        label_only,
        value="DATE TBD",
        small="ACTIVE AWARD · PERIOD OF PERFORMANCE END",
        machine_evidence={"source_kind": "usaspending-award"},
    )

    for row in (source_only, label_only, malformed):
        model["horizon"] = [row]
        html = sb.render_signal_board(model)
        assert "1 CITED FORWARD EVENT · NOT QUALIFIED PIPELINE" in html
        assert "The first source-stated event" not in html


def test_no_cross_client_leakage():
    """P1-1: the template holds no client data; an ACME report is pure ACME."""
    html = sb.render_signal_board(_full_model())
    for leaked in ("Insignary", "Accenture Federal", "DV United", "Blue Phoenix",
                   "Galois", "$71.23M obligated · 2 active orders"):
        assert leaked not in html, f"cross-client leakage: {leaked!r}"
    assert "ACME CORP" in html


def test_structured_external_entities_are_not_mail_merge_bleed(monkeypatch):
    """Only a cited competitor identity is exempt; the card's authored
    small/wedge copy stays inside both outbound-copy lint boundaries."""
    from agents.reports import lint as lint_mod

    model = _full_model()
    model["competitors"][0]["title"] = "NETSCOUT"
    monkeypatch.setattr(
        lint_mod, "known_client_terms", lambda: {"NETSCOUT": ["NETSCOUT"]})
    html = sb.render_signal_board(model)
    assert 'class="sb-competitor" data-thirdparty="1"' not in html
    assert re.search(
        r'<a data-thirdparty="1"[^>]*>NETSCOUT</a>', html)
    assert lint_mod.lint_client_bleed(html, "ACME CORP").ok

    uncited = _full_model()
    uncited["competitors"][0]["title"] = "NETSCOUT"
    uncited["competitors"][0]["url"] = ""
    uncited_html = sb.render_signal_board(uncited)
    assert not re.search(
        r'<a data-thirdparty="1"[^>]*>NETSCOUT</a>', uncited_html)
    assert not lint_mod.lint_client_bleed(uncited_html, "ACME CORP").ok

    for field in ("small", "wedge"):
        tainted = _full_model()
        tainted["competitors"][0]["title"] = "NETSCOUT"
        tainted["competitors"][0][field] = "Authored NETSCOUT strategy"
        rendered = sb.render_signal_board(tainted)
        assert not lint_mod.lint_client_bleed(rendered, "ACME CORP").ok

    vendor = _full_model()
    vendor["competitors"][0]["title"] = "OpenAI"
    assert lint_mod.lint_whitelabel(sb.render_signal_board(vendor)).ok
    for field in ("small", "wedge"):
        tainted = _full_model()
        tainted["competitors"][0]["title"] = "OpenAI"
        tainted["competitors"][0][field] = "Authored OpenAI strategy"
        assert not lint_mod.lint_whitelabel(
            sb.render_signal_board(tainted)).ok


def test_cited_structured_horizon_company_title_is_not_client_bleed(
        monkeypatch):
    """A sourced company identity is evidence, not composed client copy."""
    from agents.reports import lint as lint_mod

    model = _full_model()
    model["horizon"][0].update({
        "title": "Recorded Future",
        "url": "https://www.usaspending.gov/award/CONT_AWD_EXAMPLE",
        "organization_marks": [{
            "kind": "agency", "label": "DARPA", "display": "DARPA",
            "logo": "",
        }, {
            "kind": "company", "label": "Recorded Future, Inc.",
            "display": "Recorded Future", "logo": "",
        }],
    })
    monkeypatch.setattr(
        lint_mod, "known_client_terms",
        lambda: {"Recorded Future": ["Recorded Future"]},
    )

    html = sb.render_signal_board(model)

    assert re.search(
        r'<a data-thirdparty="1"[^>]*>Recorded Future</a>', html)
    assert lint_mod.lint_client_bleed(html, "ACME CORP").ok

    model["horizon"][0]["small"] = "Authored Recorded Future strategy"
    assert not lint_mod.lint_client_bleed(
        sb.render_signal_board(model), "ACME CORP").ok

    model["horizon"][0]["small"] = "OBLIGATED"
    model["horizon"][0]["url"] = ""
    uncited_html = sb.render_signal_board(model)
    assert not re.search(
        r'<a data-thirdparty="1"[^>]*>Recorded Future</a>', uncited_html)
    assert not lint_mod.lint_client_bleed(
        uncited_html, "ACME CORP").ok


def test_render_is_deterministic():
    m = _full_model()
    assert sb.render_signal_board(m) == sb.render_signal_board(m)


def test_public_route_and_ticker_labels_and_blank_naics_copy():
    model = _full_model()
    model["scale"]["rows"].append({
        "label": "Second corridor",
        "url": "https://www.usaspending.gov/award/Q",
        "naics": "   ",
        "amount": "$2M",
    })

    html = sb.render_signal_board(model)

    assert "Priority federal routes" in html
    assert "Active award signals · hover to pause" in html
    assert 'aria-label="Active federal award signals"' in html
    assert html.count("NAICS 541512") == 1
    assert "NAICS </small>" not in html


def test_teaming_band_meta_is_derived_from_rendered_card_kinds():
    model = _full_model()
    assert "1 OPERATOR CANDIDATE" in \
        sb.render_signal_board(model)

    entry = dict(model["teaming"][0])
    entry["machine_evidence"] = {
        "source_kind": "corridor-entry-thesis",
    }
    entry["award_generated_id"] = "CONT_AWD_ENTRY"
    evidenced = dict(model["teaming"][0])
    evidenced["machine_evidence"] = {
        "source_kind": "usaspending-subaward",
    }
    model["teaming"] = [evidenced, dict(evidenced), entry]

    html = sb.render_signal_board(model)
    assert ("2 MATCHED PRIME-SUBAWARD RECORDS · "
            "1 CITED AWARD-HOLDER ROUTE") in html
    assert "VALIDATION-REQUIRED" not in html
    assert "EVIDENCED SUBAWARD ROUTE" not in html


def test_entry_thesis_exposes_its_structured_award_citation_on_card():
    model = _full_model()
    gid = "CONT_AWD_2032H525F00130_2050_NNG15SC71B_8000"
    model["teaming"][0].update({
        "award_generated_id": gid,
        "citation_label": "award 2032H525F00130",
        "machine_evidence": {
            "source_kind": "corridor-entry-thesis",
            "source_identity": gid,
        },
    })
    materialized = sb._materialize_federal_links(
        model, {("usaspending_award", gid): True})

    html = sb.render_signal_board(materialized)

    assert (f'href="https://www.usaspending.gov/award/{gid}"' in html)
    assert 'data-link-builder="usaspending_award"' in html
    assert f'data-link-record="{gid}"' in html
    assert 'data-link-reconciled="1">award 2032H525F00130</a>' in html


@pytest.mark.parametrize("mutate,expected", [
    (lambda html: html.replace(
        "data:image/svg+xml;base64,PHN2Zy8+", "", 1),
     "client header logo"),
    (lambda html: html.replace(
        'class="sb-news-ticker"', 'class="ticker-removed"', 1),
     "ticker container"),
    (lambda html: html.replace(
        'aria-label="Active federal award signals"',
        'aria-label="removed"', 1),
     "accessible label"),
    (lambda html: html.replace(
        "Active award signals · hover to pause", "removed", 1),
     "visible label"),
    (lambda html: html.replace("sb-news-dup", "ticker-duplicate-removed"),
     "duplicated tape"),
    (lambda html: html.replace("animation: sb-news-scroll", "animation: none"),
     "ticker animation"),
    (lambda html: html.replace("@keyframes sb-news-scroll", "@keyframes removed"),
     "ticker keyframes"),
    (lambda html: html.replace(
        "animation-play-state: paused", "animation-play-state: running"),
     "pause control"),
    (lambda html: html.replace(
        "@media (prefers-reduced-motion: reduce)",
        "@media (prefers-reduced-motion: no-preference)"),
     "reduced-motion fallback"),
    (lambda html: html.replace(
        ".sb-news-dup { display: none; }",
        ".sb-news-dup { display: flex; }"),
     "duplicate rule"),
])
def test_authority_and_motion_are_release_linted(mutate, expected):
    html = mutate(sb.render_signal_board(_full_model()))

    ok, violations = sb.lint_signal_board(html)

    assert not ok
    assert any(expected in violation for violation in violations), violations


def test_build_model_uses_presentation_name_without_changing_asset_identity(
        monkeypatch):
    from tools.capability import CapabilityTerms, ClientProfile
    from agents.reports import report_assets

    profile = ClientProfile(
        client_name="mark43",
        display_name="Mark43",
        capability_terms=CapabilityTerms(core=["dispatch"]),
        naics_boundary=["541512"],
    )
    monkeypatch.setattr("tools.capability.load_profile", lambda _name: profile)
    logo_lookups = []

    def logo_for(name):
        logo_lookups.append(name)
        return ""

    monkeypatch.setattr(report_assets, "client_logo", logo_for)
    doc = SimpleNamespace(client_name="mark43", counts=lambda: {})
    content = SimpleNamespace(
        hero_context=("We screened primary federal records for active "
                      "delivery aligned with mark43's Mark43 capabilities."),
        chips=[], scale_total="", scale_counts="", scale=None, pocs=[],
        sequence="", news=[], signal_cards=[], coverage=[], best_fit=[],
        competitors=[], teaming=[], horizon=[], evidence=[],
    )

    model = sb.build_model(
        doc,
        report_date="19 JUL 2026",
        content=content,
        figures=[{"retrieved_at": "2026-07-19T00:00:00+00:00"}],
    )
    html = sb.render_signal_board(model)

    assert model["client_name"] == "Mark43"
    assert "Mark43's Mark43 capabilities" in model["hero_context"]
    assert "mark43's" not in model["hero_context"]
    assert "<title>Mark43 · Federal Opportunity Pre-Assessment</title>" in html
    assert 'alt="Mark43 logo"' in html
    assert logo_lookups and set(logo_lookups) == {"mark43"}


def test_build_model_resolves_composed_horizon_marks_for_client(monkeypatch):
    from agents.reports import report_assets
    from agents.reports.board_content import SignalBoardContent

    monkeypatch.setattr(
        "tools.capability.client_display_name", lambda name: name)
    monkeypatch.setattr(report_assets, "client_logo", lambda _name: "")
    lookups = []

    def seal_for(agency, client_name=None):
        lookups.append((agency, client_name))
        return "data:image/png;base64,SEAL="

    monkeypatch.setattr(report_assets, "agency_seal", seal_for)
    content = SignalBoardContent(
        client_name="Testco",
        horizon=[{
            "hot": True,
            "label": "IRS · AWARD WINDOW",
            "agency_name": "Department of the Treasury",
            "title": "Riverbed via RedSky",
            "value": "31 JUL 2026",
            "small": "ACTIVE AWARD",
        }],
    )
    model = sb.build_model(
        SimpleNamespace(client_name="Testco", counts=lambda: {}),
        report_date="19 JUL 2026",
        content=content,
        figures=[{"retrieved_at": "2026-07-19T00:00:00+00:00"}],
    )

    assert lookups == [("Department of the Treasury", "Testco")]
    marks = model["horizon"][0]["organization_marks"]
    assert marks == [{
        "kind": "agency",
        "label": "Department of the Treasury",
        "display": "IRS",
        "logo": "data:image/png;base64,SEAL=",
    }]
    html = sb.render_signal_board(model)
    assert 'data-brand-key="agency:treas"' in html
    assert 'class="sb-horizon-logo"' in html


def test_machine_content_empty_bands_never_resurrect_derived_rows():
    """C1 owns the publication bands even when screening rejects every row.

    The assessment document deliberately has data in each legacy-derived
    surface. Empty machine bands must stay empty so press cannot reintroduce
    rejected news, competitors, teaming, horizon, or evidence.
    """
    import os
    import sys
    from datetime import date

    sys.path.insert(0, os.path.dirname(__file__))
    from agents.reports.board_content import SignalBoardContent
    from agents.reports.document import build_document
    from test_assessment_document import _searches

    doc = build_document("Testco", searches=_searches(3), qualify=None,
                         as_of=date(2026, 7, 15))
    doc.partnering.plays.append(SimpleNamespace(
        kind="pursue",
        title="Video Wall Modernization",
        candidates=[SimpleNamespace(
            company="Evidence Partner",
            agencies=["Department of Defense"],
        )],
    ))
    derived = sb.build_model(doc, report_date="15 JUL 2026")
    for band in ("news", "competitors", "teaming", "horizon", "evidence"):
        assert derived[band], f"fixture must exercise derived {band} rows"

    content = SignalBoardContent(
        client_name="Testco",
        composition_mode="machine",
        scale_label="Client-footprint obligated history",
        scale_total="$0",
        scale={
            "rows": [],
            "total": "$0",
            "basis": "client_obligated_to_date",
        },
    )
    model = sb.build_model(
        doc,
        report_date="15 JUL 2026",
        content=content,
        figures=[{"retrieved_at": "2026-07-15T00:00:00+00:00"}],
    )

    for band in (
        "news", "signal_cards", "coverage", "best_fit", "competitors",
        "teaming", "horizon", "evidence",
    ):
        assert model[band] == [], f"machine-owned {band} must replace empty"


def test_lint_rejects_empty_model():
    """P1-5: an empty model must NOT pass just because the shell is intact."""
    html = sb.render_signal_board({"client_name": "ACME", "signal_cards": [],
                                   "best_fit": [], "competitors": [], "teaming": [],
                                   "horizon": [], "news": []})
    ok, v = sb.lint_signal_board(html)
    assert not ok
    assert any("empty band" in x for x in v)


def test_lint_has_teeth_on_structure():
    good = sb.render_signal_board(_full_model())
    ok, v = sb.lint_signal_board(good.replace("$289.5M", "{{SCALE_TOTAL}}", 1))
    assert not ok and any("token" in x for x in v)
    ok, v = sb.lint_signal_board(re.sub(r"(?i)no affiliation or endorsement", "", good))
    assert not ok and any("disclaimer" in x for x in v)


def test_url_scheme_allowlist():
    """P2-6: unsafe schemes never reach an href."""
    assert sb._safe_url("javascript:alert(1)") == "#"
    assert sb._safe_url("https://sam.gov/x").startswith("https://")
    html = sb.render_signal_board(dict(_full_model(),
                                       sequence="ok",
                                       best_fit=[dict(_full_model()["best_fit"][0],
                                                      url="javascript:alert(1)")]))
    assert "javascript:" not in html


def test_agency_badge_falls_back_to_text_without_seal():
    """P2-7: no seal -> no has-image (which would hide the code) -> text code."""
    assert "has-image" not in sb._agency_badge({"code": "DHS"})
    assert "has-image" in sb._agency_badge({"code": "DHS", "seal": "data:image/png;base64,AAA"})


def test_adapter_maps_a_real_assessment_document():
    """The adapter consumes a scoped AssessmentDocument (not the Target contact
    plan) and maps its records into the model."""
    import os
    import sys
    from datetime import date
    sys.path.insert(0, os.path.dirname(__file__))
    from agents.reports.document import build_document
    from test_assessment_document import _searches

    doc = build_document("Testco", searches=_searches(3), qualify=None,
                         as_of=date(2026, 7, 15))
    model = sb.build_model(doc, report_date="15 JUL 2026")
    assert len(model["best_fit"]) == len(doc.board.pursuits) == 3
    assert len(model["signal_cards"]) == 4
    html = sb.render_signal_board(model)
    assert re.findall(r"\{\{\w+\}\}", html) == []      # every token filled
    assert "insignary" not in html.lower()             # no cross-client leak
    assert 'class="sb-opp"' in html and 'class="sb-competitor"' in html


# ── the computed data-current line (per-figure freshness, 2026-07-16) ────────

def test_data_current_is_computed_from_figure_provenance_not_report_date():
    """Doc dated 15 JUL; oldest figure pulled 14 JUL: the evidence dock says
    14 JUL. The document date can never masquerade as data currency."""
    html = sb.render_signal_board(_full_model())
    assert "data current 14 JUL 2026" in html
    assert "data current 15 JUL 2026" not in html


def test_data_current_cannot_be_hand_set():
    """A hand-set date string has no slot to land in: only figure provenance
    reaches the footer."""
    m = dict(_full_model(), data_current="01 JAN 2020", figures=[
        {"retrieved_at": "2026-07-10T00:00:00+00:00"}])
    html = sb.render_signal_board(m)
    assert "01 JAN 2020" not in html
    assert "data current 10 JUL 2026" in html


def test_missing_or_unknown_provenance_drops_the_clause_and_fails_lint():
    for figures in (None, [], [{"retrieved_at": "2026-07-10T00:00:00+00:00"},
                              {"retrieved_at": None}]):
        html = sb.render_signal_board(dict(_full_model(), figures=figures))
        assert "data current" not in html.lower()      # absent, never invented
        ok, v = sb.lint_signal_board(html)
        assert not ok
        assert any("data-current" in x for x in v)


def test_build_model_fails_before_render_on_document_date_divergence():
    """The Insignary shape: a document dated 13 JUL carrying a 02 JUN figure
    dies in the adapter, before any render exists."""
    import os
    import sys
    from datetime import date
    import pytest
    sys.path.insert(0, os.path.dirname(__file__))
    from agents.reports.document import build_document
    from test_assessment_document import _searches

    doc = build_document("Testco", searches=_searches(3), qualify=None,
                         as_of=date(2026, 7, 13))
    stale = [{"retrieved_at": "2026-06-02T00:00:00+00:00"}]
    with pytest.raises(ValueError, match="re-pull the stale figures"):
        sb.build_model(doc, report_date="13 JUL 2026", figures=stale)
    fresh = [{"retrieved_at": "2026-07-12T00:00:00+00:00"}]
    model = sb.build_model(doc, report_date="13 JUL 2026", figures=fresh)
    assert model["figures"] == fresh
    assert "data current 12 JUL 2026" in sb.render_signal_board(model)


def test_touched_template_still_holds_white_label_rules():
    """Constraint re-verified on the touched template: no vendor/internal
    vocabulary reaches a client-readable surface, with and without the
    computed clause."""
    from agents.reports.lint import lint_whitelabel
    html = sb.render_signal_board(_full_model())
    assert lint_whitelabel(html).ok
    assert "UNKNOWN_FRESHNESS" not in html
    bare = sb.render_signal_board(dict(_full_model(), figures=None))
    assert lint_whitelabel(bare).ok
    assert "UNKNOWN_FRESHNESS" not in bare
