"""C1 per-card client-relevance contract.

These tests deliberately exercise only the small exported semantic surface.
The renderer may choose where to place the public object, but machine cards
must carry copy that is a deterministic projection of source-bound CORE
evidence, and the identical basis must survive in the compose trail.
"""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from agents.reports import composer


GID = "CONT_AWD_TEST_7000_IDV_7000"
TERM = "network performance monitoring"


def _support(
    *,
    role: str = "record",
    identity: str = GID,
    term: str = TERM,
    field: str = "description",
    matched_text: str | None = None,
    quote: str | None = None,
) -> dict:
    matched = matched_text or term.upper()
    return {
        "role": role,
        "source_identity": identity,
        "field": field,
        "matched_text": matched,
        "quote": quote or f"FEDERAL {matched} PLATFORM SUPPORT",
    }


def _basis(
    kind: str = "record-core-match",
    *,
    identity: str = GID,
    term: str = TERM,
    band: str | None = None,
) -> dict:
    if band is None:
        band = "teaming" if kind in {
            "corridor-core-match", "shared-core-route"} else "competitors"
    if kind == "shared-core-route":
        supports = [
            _support(
                role="selected-play",
                identity="SAM-NOTICE-1",
                term=term,
                field="title",
            ),
            _support(
                role="subaward-route",
                identity="SUBAWARD-1",
                term=term,
            ),
        ]
        surface = "profile.capability_terms.core"
    else:
        supports = [_support(identity=identity, term=term)]
        surface = "capability_taxonomy.core"
    context = {
        "competitors": {
            "band": "competitors", "buyer": "IRS", "client": "Testco"},
        "teaming": {
            "band": "teaming", "buyer": "IRS", "client": "Testco",
            "partner": "Candidate Partner"},
        "horizon": {
            "band": "horizon", "client": "Testco",
            "client_footprint": False, "event_kind": "award-window"},
    }[band]
    return {
        "version": 1,
        "kind": kind,
        "profile_client": "testco",
        "profile_surface": surface,
        "profile_version": 2,
        "term": term,
        "supports": supports,
        "public_context": context,
    }


def _card(
    band: str,
    *,
    identity: str = GID,
    kind: str | None = None,
) -> dict:
    if kind is None:
        kind = (
            "corridor-core-match"
            if band == "teaming"
            else "record-core-match"
        )
    basis = _basis(kind, identity=identity, band=band)
    public = composer.client_relevance_public(basis)
    machine = {
        "source_identity": identity,
        "source_kind": (
            "corridor-entry-thesis"
            if band == "teaming"
            else "usaspending-award"
        ),
        "client_relevance_basis": basis,
    }
    if band == "competitors":
        machine.update({
            "quote": f"FEDERAL {TERM.upper()} PLATFORM SUPPORT",
            "agency": "Department of the Treasury",
        })
        return {
            "label": "IRS · ACTIVE",
            "title": "CITED VENDOR",
            "award_generated_id": identity,
            "client_relevance": public,
            "machine_evidence": machine,
        }
    if band == "teaming":
        if kind == "shared-core-route":
            machine.update({
                "source_identity": (
                    f"{identity}::subaward::SUBAWARD-1"),
                "source_kind": "usaspending-subaward",
                "route_basis": {
                    "kind": "subaward-evidence",
                    "partner": "Candidate Partner",
                    "agency": "Internal Revenue Service",
                    "matched_naics": "541512",
                    "shared_core_terms": [TERM],
                    "selected_play_identity": "SAM-NOTICE-1",
                    "selected_play_label": "Network monitoring",
                    "subaward_id": "SUBAWARD-1",
                    "prime_award_id": "AWARD-1",
                    "award_generated_id": identity,
                    "edge_count": 1,
                },
            })
        else:
            machine["route_basis"] = {
                "kind": "entry-thesis",
                "partner": "Candidate Partner",
                "route": TERM,
                "buyer": "Internal Revenue Service",
                "pop_end": "2027-01-31",
                "award_id": "AWARD-1",
                "award_generated_id": identity,
            }
        return {
            "partner": "CANDIDATE PARTNER",
            "client": "TESTCO",
            "award_generated_id": identity,
            "client_relevance": public,
            "machine_evidence": machine,
        }
    if band == "horizon":
        machine["job_context_basis"] = {
            "version": 1,
            "event_kind": "award-window",
            "source_field": "description",
            "source_text": f"FEDERAL {TERM.upper()} PLATFORM SUPPORT",
        }
        return {
            "title": "CITED ACCOUNT",
            "organization_marks": [],
            "client_relevance": public,
            "machine_evidence": machine,
        }
    raise AssertionError(f"unknown test band {band!r}")


def _content(**bands: list[dict]) -> dict:
    return {
        "composition_mode": "machine",
        "client_name": "Testco",
        "competitors": bands.get("competitors", []),
        "teaming": bands.get("teaming", []),
        "horizon": bands.get("horizon", []),
    }


def _trail_line(band: str, card: dict, *, basis: dict | None = None) -> str:
    machine = card["machine_evidence"]
    envelope = {
        "band": band,
        "card_identity": machine["source_identity"],
        "basis": basis or machine["client_relevance_basis"],
    }
    return "  - client relevance " + json.dumps(
        envelope,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _trail(*rows: tuple[str, dict]) -> str:
    return "\n".join(_trail_line(band, card) for band, card in rows) + "\n"


PUBLIC_PROJECTION_CASES = [
    pytest.param(
        "record-core-match",
        "competitors",
        {},
        {
            "kind": "record-core-match",
            "text": (
                "DISPLACEMENT TARGET · IRS currently funds NETWORK "
                "PERFORMANCE MONITORING; prioritize this incumbent account "
                "for Testco replacement and follow-on capture."
            ),
        },
        id="competitor-displacement",
    ),
    pytest.param(
        "corridor-core-match",
        "teaming",
        {},
        {
            "kind": "corridor-core-match",
            "text": (
                "PARTNER DECISION · Candidate Partner holds the cited IRS "
                "NETWORK PERFORMANCE MONITORING award; target that incumbent "
                "channel for a Testco partner-or-displace decision."
            ),
        },
        id="teaming-award-holder",
    ),
    pytest.param(
        "shared-core-route",
        "teaming",
        {},
        {
            "kind": "shared-core-route",
            "text": (
                "PRIME-CHANNEL SIGNAL · Candidate Partner has matched "
                "opportunity and subaward evidence for NETWORK PERFORMANCE "
                "MONITORING; prioritize this prime for Testco teaming "
                "outreach."
            ),
        },
        id="teaming-evidenced-prime",
    ),
    pytest.param(
        "record-core-match",
        "horizon",
        {"event_kind": "award-window", "client_footprint": False},
        {
            "kind": "record-core-match",
            "text": (
                "FOLLOW-ON CHECK · Incumbent NETWORK PERFORMANCE MONITORING "
                "work reaches period end. That date is not a confirmed "
                "recompete; confirm option, extension, follow-on, "
                "replacement, or sunset before assigning Testco takeout "
                "action."
            ),
        },
        id="horizon-award-window-non-client",
    ),
    pytest.param(
        "record-core-match",
        "horizon",
        {"event_kind": "award-window", "client_footprint": True},
        {
            "kind": "record-core-match",
            "text": (
                "CONTINUITY CHECK · Testco's cited NETWORK PERFORMANCE "
                "MONITORING footprint reaches period end. That date is not a "
                "confirmed recompete; confirm option, extension, follow-on, "
                "replacement, or sunset before assigning capture action."
            ),
        },
        id="horizon-award-window-client",
    ),
    pytest.param(
        "record-core-match",
        "horizon",
        {"event_kind": "forecast", "client_footprint": False},
        {
            "kind": "record-core-match",
            "text": (
                "PRE-SOLICITATION MOVE · The cited forecast matches NETWORK "
                "PERFORMANCE MONITORING; engage the buyer and place Testco "
                "ahead of the stated acquisition milestone."
            ),
        },
        id="horizon-forecast",
    ),
    pytest.param(
        "record-core-match",
        "horizon",
        {"event_kind": "expiring", "client_footprint": False},
        {
            "kind": "record-core-match",
            "text": (
                "COMPLETION SIGNAL · Cited NETWORK PERFORMANCE MONITORING "
                "work approaches contract completion; validate options, "
                "extension, and the follow-on acquisition path before "
                "assigning Testco capture action."
            ),
        },
        id="horizon-expiring",
    ),
]


@pytest.mark.parametrize(
    ("kind", "band", "context_overrides", "expected"),
    PUBLIC_PROJECTION_CASES,
)
def test_public_projection_is_exact_and_deterministic(
        kind, band, context_overrides, expected):
    basis = _basis(kind, band=band)
    basis["public_context"].update(context_overrides)
    pristine = copy.deepcopy(basis)
    reordered = dict(reversed(list(copy.deepcopy(basis).items())))

    assert composer.client_relevance_public(basis) == expected
    assert composer.client_relevance_public(reordered) == expected
    assert basis == pristine, "projection must not mutate its basis"


@pytest.mark.parametrize(
    "forbidden",
    [
        "qualify",
        "unverified",
        "validation-required",
        "role validation",
        "not an open bid",
        "successor decision",
        "evidenced prime path",
        "evidenced subaward route",
    ],
)
def test_machine_client_relevance_public_copy_bans_weak_language(forbidden):
    rendered = []
    for kind, band, context_overrides, _expected in [
            case.values[:4] for case in PUBLIC_PROJECTION_CASES]:
        basis = _basis(kind, band=band)
        basis["public_context"].update(context_overrides)
        rendered.append(composer.client_relevance_public(basis)["text"])

    assert forbidden not in "\n".join(rendered).casefold()


def test_public_projection_requires_client_specific_context():
    basis = _basis()
    basis.pop("public_context")

    with pytest.raises(ValueError, match="client relevance"):
        composer.client_relevance_public(basis)


@pytest.mark.parametrize("band", ["competitors", "teaming", "horizon"])
@pytest.mark.parametrize("missing", ["public", "basis"])
def test_every_machine_card_requires_public_copy_and_structured_basis(
    band, missing
):
    card = _card(band)
    if missing == "public":
        card.pop("client_relevance")
    else:
        card["machine_evidence"].pop("client_relevance_basis")

    with pytest.raises(ValueError, match="client relevance"):
        composer.validate_machine_client_relevance_contract(
            _content(**{band: [card]})
        )


@pytest.mark.parametrize("band", ["competitors", "teaming", "horizon"])
def test_public_copy_tamper_is_refused_for_every_band(band):
    card = _card(band)
    card["client_relevance"]["text"] = "Strategic fit is obvious."

    with pytest.raises(ValueError, match="client relevance"):
        composer.validate_machine_client_relevance_card(card)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda basis: basis["supports"][0].update(
            quote="UNRELATED JANITORIAL WORK"
        ),
        lambda basis: basis["supports"][0].update(
            source_identity="A-DIFFERENT-AWARD"
        ),
        lambda basis: basis.update(term="invented capability"),
    ],
)
def test_basis_tamper_is_refused(mutate):
    card = _card("competitors")
    mutate(card["machine_evidence"]["client_relevance_basis"])

    with pytest.raises(ValueError, match="client relevance"):
        composer.validate_machine_client_relevance_card(card)


def test_complete_trail_accepts_same_gid_in_all_three_bands():
    competitor = _card("competitors", identity=GID)
    teaming = _card("teaming", identity=GID)
    horizon = _card("horizon", identity=GID)
    content = _content(
        competitors=[competitor],
        teaming=[teaming],
        horizon=[horizon],
    )
    trail = _trail(
        ("competitors", competitor),
        ("teaming", teaming),
        ("horizon", horizon),
    )

    composer.validate_machine_client_relevance_contract(
        content,
        trail_md=trail,
    )


@pytest.mark.parametrize("defect", ["missing", "extra", "duplicate"])
def test_trail_set_must_match_cards_exactly(defect):
    competitor = _card("competitors", identity=GID)
    teaming = _card("teaming", identity=GID)
    horizon = _card("horizon", identity=GID)
    content = _content(
        competitors=[competitor],
        teaming=[teaming],
        horizon=[horizon],
    )
    rows = [
        ("competitors", competitor),
        ("teaming", teaming),
        ("horizon", horizon),
    ]
    if defect == "missing":
        rows.pop()
    elif defect == "extra":
        rows.append(
            ("competitors", _card("competitors", identity="EXTRA-GID"))
        )
    else:
        rows.append(("competitors", competitor))

    with pytest.raises(ValueError, match="client relevance"):
        composer.validate_machine_client_relevance_contract(
            content,
            trail_md=_trail(*rows),
        )


def test_trail_basis_tamper_is_refused_even_when_public_card_is_unchanged():
    card = _card("horizon")
    trail_basis = copy.deepcopy(
        card["machine_evidence"]["client_relevance_basis"]
    )
    trail_basis["supports"][0]["quote"] = "UNRELATED TRAIL TEXT"
    trail = _trail_line("horizon", card, basis=trail_basis) + "\n"

    with pytest.raises(ValueError, match="client relevance"):
        composer.validate_machine_client_relevance_contract(
            _content(horizon=[card]),
            trail_md=trail,
        )


@pytest.mark.parametrize(
    ("band", "mutate"),
    [
        ("competitors", lambda card: card["machine_evidence"][
            "client_relevance_basis"]["public_context"].update(
                buyer="Another agency")),
        ("teaming", lambda card: card["machine_evidence"][
            "client_relevance_basis"]["public_context"].update(
                partner="Another partner")),
        ("horizon", lambda card: card["machine_evidence"][
            "client_relevance_basis"]["public_context"].update(
                event_kind="forecast")),
        ("horizon", lambda card: card["machine_evidence"][
            "client_relevance_basis"]["public_context"].update(
                client_footprint=True)),
    ],
)
def test_public_context_must_match_the_card_source_fields(band, mutate):
    card = _card(band)
    mutate(card)
    basis = card["machine_evidence"]["client_relevance_basis"]
    card["client_relevance"] = composer.client_relevance_public(basis)

    with pytest.raises(ValueError, match="client relevance"):
        composer.validate_machine_client_relevance_card(card)


def test_shared_route_support_identities_must_match_the_actual_route():
    card = _card("teaming", kind="shared-core-route")
    basis = card["machine_evidence"]["client_relevance_basis"]
    basis["supports"][0]["source_identity"] = "OTHER-NOTICE"
    card["client_relevance"] = composer.client_relevance_public(basis)

    with pytest.raises(ValueError, match="shared route"):
        composer.validate_machine_client_relevance_card(card)


def _current_inputs():
    from tools.relevance.taxonomy import CapabilityTaxonomy, TaxonomyTerm

    taxonomy = CapabilityTaxonomy(
        client_name="Testco", version=2, updated="2026-07-20",
        core=[TaxonomyTerm(term=TERM)],
    )
    profile = SimpleNamespace(
        client_name="Testco",
        capability_terms=SimpleNamespace(core=[TERM]),
    )
    sweep = {"results": {"incumbent_buyer_map": {"buyers": [{
        "buyer": "Internal Revenue Service",
        "agency": "Department of the Treasury",
        "records": [{
            "kind": "award",
            "recipient": "CITED VENDOR",
            "generated_internal_id": GID,
            "award_id": "AWARD-1",
            "description": f"FEDERAL {TERM.upper()} PLATFORM SUPPORT",
        }],
    }]}}}
    return taxonomy, profile, sweep


def test_current_taxonomy_and_stored_source_are_required_at_press_strength():
    taxonomy, profile, sweep = _current_inputs()
    card = _card("competitors")
    content = _content(competitors=[card])
    composer.validate_machine_client_relevance_contract(
        content, taxonomy=taxonomy, profile=profile, sweep=sweep)

    fabricated = copy.deepcopy(card)
    basis = fabricated["machine_evidence"]["client_relevance_basis"]
    basis.update(term="invented capability")
    basis["supports"][0].update(
        matched_text="INVENTED CAPABILITY",
        quote="FEDERAL INVENTED CAPABILITY PLATFORM SUPPORT",
    )
    fabricated["machine_evidence"]["quote"] = \
        "FEDERAL INVENTED CAPABILITY PLATFORM SUPPORT"
    fabricated["client_relevance"] = composer.client_relevance_public(basis)

    with pytest.raises(ValueError, match="current client CORE"):
        composer.validate_machine_client_relevance_contract(
            _content(competitors=[fabricated]),
            taxonomy=taxonomy, profile=profile, sweep=sweep)


def test_long_contractor_display_projection_remains_source_bound():
    taxonomy, profile, sweep = _current_inputs()
    long_name = "General Dynamics Information Technology"
    public_name = "General Dynamics Information"
    sweep["results"]["incumbent_buyer_map"]["buyers"][0][
        "records"][0]["recipient"] = long_name

    competitor = _card("competitors")
    competitor["title"] = public_name

    teaming = _card("teaming")
    teaming["partner"] = public_name.upper()
    teaming["machine_evidence"]["quote"] = \
        f"FEDERAL {TERM.upper()} PLATFORM SUPPORT"
    teaming["machine_evidence"]["route_basis"]["partner"] = long_name
    team_basis = teaming["machine_evidence"]["client_relevance_basis"]
    team_basis["public_context"]["partner"] = public_name
    teaming["client_relevance"] = composer.client_relevance_public(team_basis)

    assert composer._display_company(long_name, 34) == public_name
    composer.validate_machine_client_relevance_contract(
        _content(competitors=[competitor], teaming=[teaming]),
        taxonomy=taxonomy,
        profile=profile,
        sweep=sweep,
    )


def test_truncated_partner_collision_cannot_rebind_to_another_recipient():
    taxonomy, profile, sweep = _current_inputs()
    route_partner = "General Dynamics Information Technology Alpha"
    stored_recipient = "General Dynamics Information Technology Bravo"
    display = composer._display_company(route_partner, 34)
    assert display == composer._display_company(stored_recipient, 34)
    sweep["results"]["incumbent_buyer_map"]["buyers"][0][
        "records"][0]["recipient"] = stored_recipient

    card = _card("teaming")
    card["partner"] = display.upper()
    route = card["machine_evidence"]["route_basis"]
    route["partner"] = route_partner
    card["machine_evidence"]["quote"] = \
        f"FEDERAL {TERM.upper()} PLATFORM SUPPORT"
    basis = card["machine_evidence"]["client_relevance_basis"]
    basis["public_context"]["partner"] = display
    card["client_relevance"] = composer.client_relevance_public(basis)

    with pytest.raises(ValueError, match="teaming context"):
        composer.validate_machine_client_relevance_contract(
            _content(teaming=[card]),
            taxonomy=taxonomy,
            profile=profile,
            sweep=sweep,
        )


def _shared_route_current_inputs():
    taxonomy, profile, _sweep = _current_inputs()
    evidence = f"FEDERAL {TERM.upper()} PLATFORM SUPPORT"
    sweep = {
        "results": {
            "sam.gov": [{
                "source_id": "SAM-NOTICE-1",
                "title": evidence,
            }],
            "subawards": {
                "edges": {
                    "541512": [{
                        "subaward_id": "SUBAWARD-1",
                        "prime": "Candidate Partner",
                        "awarding_agency": "Internal Revenue Service",
                        "prime_award_id": "AWARD-1",
                        "prime_award_generated_id": GID,
                        "description": evidence,
                    }],
                },
            },
            "incumbent_buyer_map": {
                "buyers": [{
                    "buyer": "Internal Revenue Service",
                    "agency": "Department of the Treasury",
                    "records": [{
                        "kind": "award",
                        "recipient": "Candidate Partner",
                        "generated_internal_id": GID,
                        "award_id": "AWARD-1",
                        "description": evidence,
                    }],
                }],
            },
        },
    }
    card = _card("teaming", kind="shared-core-route")
    card["machine_evidence"]["quote"] = evidence
    return taxonomy, profile, sweep, card


@pytest.mark.parametrize(
    "defect",
    ["partner", "agency", "prime_award", "canonical_prime"],
)
def test_shared_route_context_binds_to_subaward_and_current_prime(defect):
    taxonomy, profile, sweep, card = _shared_route_current_inputs()
    route = card["machine_evidence"]["route_basis"]
    basis = card["machine_evidence"]["client_relevance_basis"]

    composer.validate_machine_client_relevance_contract(
        _content(teaming=[copy.deepcopy(card)]),
        taxonomy=taxonomy,
        profile=profile,
        sweep=sweep,
    )

    if defect == "partner":
        route["partner"] = "Another Prime"
        basis["public_context"]["partner"] = "Another Prime"
        card["partner"] = "ANOTHER PRIME"
    elif defect == "agency":
        route["agency"] = "Another Agency"
        basis["public_context"]["buyer"] = "Another Agency"
    elif defect == "prime_award":
        route["prime_award_id"] = "OTHER-AWARD"
    else:
        sweep["results"]["incumbent_buyer_map"]["buyers"][0][
            "records"][0]["recipient"] = "Another Prime"
    card["client_relevance"] = composer.client_relevance_public(basis)

    with pytest.raises(ValueError, match="shared route"):
        composer.validate_machine_client_relevance_contract(
            _content(teaming=[card]),
            taxonomy=taxonomy,
            profile=profile,
            sweep=sweep,
        )


def test_operator_legacy_cards_may_omit_client_relevance():
    legacy = {
        "composition_mode": "operator",
        "client_name": "Legacy Client",
        "competitors": [{"title": "Legacy competitor", "wedge": "Lane"}],
        "teaming": [{"partner": "Legacy partner", "angle": "Review"}],
        "horizon": [{"title": "Legacy watch", "small": "Standing"}],
    }

    composer.validate_machine_client_relevance_contract(legacy)
