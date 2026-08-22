"""Focused projection tests for per-card client relevance."""

from agents.reports import signal_board as sb


def test_client_relevance_projects_in_each_card_at_the_required_position():
    competitor = {
        "title": "FCN", "wedge": "LEGACY WEDGE",
        "client_relevance": {
            "kind": "account-watch",
            "text": "IRS funds SolarWinds in Riverbed's monitoring lane.",
        },
    }
    teaming = {
        "client": "Riverbed", "partner": "FCN", "target": "IRS account",
        "angle": "CITED AWARD", "proof": "AWARD 123",
        "client_relevance": {
            "kind": "route-hypothesis",
            "text": (
                "PARTNER DECISION · FCN holds the cited route; target that "
                "channel for a Riverbed partner-or-displace decision."
            ),
        },
    }
    horizon = {
        "label": "IRS · AWARD WINDOW", "title": "Riverbed via RedSky",
        "value": "31 JUL 2026", "small": "ACTIVE AWARD",
        "job_context": {"kind": "published-description",
                        "text": "NETWORK MONITORING SUPPORT"},
        "client_relevance": {
            "kind": "watch-signal",
            "text": (
                "TAKEOUT CLOCK · Support period ends; begin Riverbed "
                "account shaping across extension, recompete, replacement, "
                "or sunset."
            ),
        },
    }

    competitor_html = sb._competitor(competitor)
    teaming_html = sb._team(teaming, "Riverbed")
    horizon_html = sb._horizon(horizon)

    assert competitor_html.count('class="sb-client-relevance"') == 1
    assert "Client relevance</b>" in competitor_html
    assert "LEGACY WEDGE" not in competitor_html
    assert teaming_html.index("sb-team-target") < teaming_html.index(
        "sb-client-relevance") < teaming_html.index("sb-angle")
    assert horizon_html.index("sb-horizon-work") < horizon_html.index(
        "sb-client-relevance") < horizon_html.index("<strong>")
    template = sb._load_template()
    assert ".sb-client-relevance b" in template
    assert "text-transform: uppercase" in template
    relevance_css = template.split(".sb-client-relevance span", 1)[1].split(
        ".sb-team-grid", 1)[0]
    assert "display: block" in relevance_css
    assert "line-clamp" not in relevance_css
    assert "max-height" not in relevance_css


def test_client_relevance_escapes_text_and_legacy_wedge_still_renders():
    unsafe = sb._competitor({
        "title": "FCN",
        "client_relevance": {
            "kind": "account-watch",
            "text": '<script>alert("x")</script> & watch',
        },
    })
    legacy = sb._competitor({"title": "FCN", "wedge": "Legacy lane"})

    assert "<script>" not in unsafe
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; watch" in unsafe
    assert 'class="sb-wedge">Legacy lane</div>' in legacy
    assert "sb-client-relevance" not in legacy


def test_blank_or_malformed_client_relevance_preserves_legacy_wedge():
    for relevance in (None, "", "   ", {}, {"kind": "account-watch"}, []):
        html = sb._competitor({
            "title": "FCN",
            "wedge": "Legacy lane",
            "client_relevance": relevance,
        })

        assert 'class="sb-wedge">Legacy lane</div>' in html
        assert "sb-client-relevance" not in html


def test_plain_string_client_relevance_is_operator_tolerant():
    html = sb._team({
        "client": "Riverbed", "partner": "FCN", "target": "IRS account",
        "angle": "CITED AWARD", "proof": "AWARD 123",
        "client_relevance": "Route fit requires validation.",
    })

    assert "Route fit requires validation." in html
    assert html.count('class="sb-client-relevance"') == 1
