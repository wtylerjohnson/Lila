"""Grants.gov / SBIR / GDELT / DoD-contracts adapters + the provenance block."""


import json

import pytest

import tools.api.dod_contracts as dc
import tools.api.gdelt as gd
import tools.api.grants_gov as gg
import tools.api.sbir_gov as sb
from tools.api import REGISTRY, SourceQuery
from tools.api.dod_contracts import DodContractsSource
from tools.api.gdelt import GdeltSource
from tools.api.grants_gov import GrantsGovSource
from tools.api.sbir_gov import SbirSource


def test_all_registered():
    for name in ("grants_gov", "sbir_gov", "gdelt", "dod_contracts"):
        assert REGISTRY.get(name) is not None, name


_DOD_RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
<item><title>Contracts For July 8, 2026</title>
  <link>https://www.defense.gov/News/Contracts/Contract/Article/1001/</link>
  <pubDate>Tue, 08 Jul 2026 16:00:00 GMT</pubDate>
  <description>&lt;p&gt;General Dynamics Information Technology was awarded a
  $45,000,000 contract for aviation risk management data services for the Air
  Force.&lt;/p&gt;</description></item>
<item><title>Contracts For July 7, 2026</title>
  <link>https://www.defense.gov/News/Contracts/Contract/Article/1000/</link>
  <pubDate>Mon, 07 Jul 2026 16:00:00 GMT</pubDate>
  <description>Lawn mowing services at Fort Example.</description></item>
</channel></rss>"""


def test_dod_contracts_keyword_filters_records_why_and_cites(monkeypatch):
    """The DoD award wire keeps only announcements hitting capability terms,
    records WHICH terms hit (the why survives to the fact bank), strips HTML
    from bodies, and every kept item carries its defense.gov link."""
    monkeypatch.setattr(dc, "get_text", lambda url, **kw: _DOD_RSS)
    out = DodContractsSource().enrich(
        SourceQuery(keywords=["aviation risk management", "OSINT"]))
    assert out["total_announcements"] == 2
    assert len(out["items"]) == 1                      # mowing filtered out
    item = out["items"][0]
    assert item["url"].startswith("https://www.defense.gov/")
    assert item["matched"] == ["aviation risk management"]
    assert "<p>" not in item["body"]                   # HTML stripped
    assert "General Dynamics" in item["body"]


def test_dod_contracts_dead_feed_never_kills_a_sweep(monkeypatch):
    def boom(url, **kw):
        raise RuntimeError("feed down")
    monkeypatch.setattr(dc, "get_text", boom)
    out = DodContractsSource().enrich(SourceQuery(keywords=["x"]))
    assert out["items"] == [] and "error" in out


def test_dod_contracts_malformed_feed_is_failure_but_valid_empty_feed_is_clean(
        monkeypatch):
    monkeypatch.setattr(dc, "get_text", lambda url, **kw: "not XML")
    out = DodContractsSource().enrich(SourceQuery(keywords=["x"]))
    assert out["items"] == []
    assert "error" in out
    assert dc._parse_items("<rss><channel /></rss>") == []
    with pytest.raises(ValueError, match="not an RSS feed"):
        dc._parse_items("<html><body>maintenance</body></html>")


def test_grants_parses_and_links(monkeypatch):
    from datetime import datetime, timezone

    fixed = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(gg, "_utc_now", lambda: fixed)
    monkeypatch.setattr(gg, "post_json", lambda url, *, json, **kw: {
        "data": {"oppHits": [{"id": 358800, "number": "DHS-26-CYB-001",
                              "title": "State Cyber Grant", "agencyName": "DHS",
                              "oppStatus": "posted", "closeDate": "2026-09-01"}]}})
    out = GrantsGovSource().enrich(SourceQuery(keywords=["cyber grant"]))
    row = out["cyber grant"][0]
    assert row["url"] == "https://www.grants.gov/search-results-detail/358800"
    assert row["agency"] == "DHS" and row["status"] == "posted"
    assert out["records"] == [{
        "record_id": "grants_gov:358800",
        "canonical_url": (
            "https://www.grants.gov/search-results-detail/358800"
        ),
        "source": "grants_gov",
        "tier": "program",
        "kind": "assistance_opportunity",
        "agency": "DHS",
        "title": "State Cyber Grant",
        "number": "DHS-26-CYB-001",
        "status": "posted",
        "open_date": None,
        "close_date": "2026-09-01",
        "matched_terms": ["cyber grant"],
        "promotion_eligible": False,
        "live_solicitation": False,
        "retrieved_at": "2026-07-20T12:00:00+00:00",
        "data_as_of": "2026-07-20",
        "agency_name": "DHS",
    }]
    assert out["_provenance"]["consumer"] == "prospective_horizon"
    assert out["_provenance"]["records_received"] == 1


def test_grants_search2_paginates_and_normalizes_real_agency_shape(monkeypatch):
    monkeypatch.setattr(gg, "ROWS_PER_PAGE", 2)
    calls = []

    def fake_post(_url, *, json, **_kwargs):
        calls.append(json["startRecordNum"])
        rows = [{
            "id": 100,
            "number": "HHS-26-1",
            "title": "Civilian health network modernization",
            "agencyCode": "HHS",
            "agencyName": "Health & Human Services",
            "openDate": "07/20/2026",
            "closeDate": "09/30/2026",
            "oppStatus": "posted",
        }, {
            "id": 101,
            "number": "DOT-26-1",
            "title": "Transportation network modernization",
            "agencyCode": "DOT",
            "agencyName": "Department of Transportation",
            "openDate": "07/21/2026",
            "closeDate": "10/01/2026",
            "oppStatus": "forecasted",
        }] if json["startRecordNum"] == 0 else [{
            "id": 102,
            "number": "DOJ-26-1",
            "title": "Justice network modernization",
            "agencyCode": "DOJ",
            "agencyName": "Department of Justice",
            "openDate": "2026-07-22",
            "closeDate": "2026-10-02",
            "oppStatus": "posted",
        }]
        return {"data": {
            "hitCount": 3,
            "startRecord": json["startRecordNum"],
            "oppHits": rows,
        }}

    monkeypatch.setattr(gg, "post_json", fake_post)
    out = GrantsGovSource().enrich(SourceQuery(keywords=["network"]))

    assert calls == [0, 2]
    assert out["records"][0]["agency"] == "HHS"
    assert out["records"][0]["agency_name"] == "Health & Human Services"
    assert out["records"][0]["open_date"] == "2026-07-20"
    assert out["records"][0]["close_date"] == "2026-09-30"
    assert out["_provenance"]["partial"] is False
    assert out["_provenance"]["source_attempts"][0]["pages"] == 2


def test_grants_discloses_keyword_and_page_caps(monkeypatch):
    monkeypatch.setattr(gg, "ROWS_PER_PAGE", 2)
    monkeypatch.setattr(gg, "MAX_PAGES_PER_KEYWORD", 1)
    monkeypatch.setattr(gg, "MAX_KEYWORDS", 1)
    monkeypatch.setattr(gg, "post_json", lambda *_args, **_kwargs: {
        "data": {
            "hitCount": 5,
            "oppHits": [{
                "id": 1,
                "title": "Network grant",
                "agencyCode": "HHS",
                "agencyName": "Health & Human Services",
            }, {
                "id": 2,
                "title": "Second network grant",
                "agencyCode": "DOT",
                "agencyName": "Department of Transportation",
            }],
        },
    })

    out = GrantsGovSource().enrich(
        SourceQuery(keywords=["network", "telemetry"])
    )

    provenance = out["_provenance"]
    assert provenance["status"] == "partial"
    assert provenance["partial"] is True
    assert "truncated" not in provenance
    assert "keyword lanes capped" in provenance["limitations"]
    assert "paging capped at 2 of 5 hits" in provenance["limitations"]


def test_grants_full_page_without_hit_count_is_named_partial(monkeypatch):
    monkeypatch.setattr(gg, "ROWS_PER_PAGE", 2)
    monkeypatch.setattr(gg, "post_json", lambda *_args, **_kwargs: {
        "data": {"oppHits": [{
            "id": 1,
            "title": "First network grant",
            "agencyCode": "HHS",
        }, {
            "id": 2,
            "title": "Second network grant",
            "agencyCode": "DOT",
        }]},
    })

    out = GrantsGovSource().enrich(SourceQuery(keywords=["network"]))

    provenance = out["_provenance"]
    assert provenance["status"] == "partial"
    assert provenance["source_attempts"][0]["status"] == "partial"
    assert "without Search2 hitCount" in provenance["limitations"]


def test_sbir_parses_list_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("LILA_SBIR_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(sb, "get_json", lambda url, *, params, **kw: [
        {"solicitation_title": "AF Cyber Topic", "solicitation_number": "AF26.1",
         "agency": "DOD", "close_date": "2026-08-20",
         "sbir_solicitation_link": "https://www.sbir.gov/node/1",
         "solicitation_topics": [{"t": 1}, {"t": 2}]}])
    out = SbirSource().enrich(SourceQuery(keywords=["cyber"]))
    row = out["cyber"][0]
    assert row["title"] == "AF Cyber Topic" and row["topic_count"] == 2
    assert row["freshness"] == "live"
    assert out["_provenance"]["mode"] == "live_official_api"
    assert out["records"][0]["record_id"].startswith("sbir_gov:AF26.1:")
    assert out["records"][0]["tier"] == "program"
    assert out["records"][0]["live_solicitation"] is False


def test_sbir_items_reject_false_clean_response_and_accept_explicit_empty():
    with pytest.raises(ValueError, match="requires results or data"):
        sb._items({"message": "maintenance"})
    with pytest.raises(ValueError, match="omitted number or title"):
        sb._items({"results": [{"message": "maintenance"}]})
    assert sb._items({"results": []}) == []
    assert sb._items({"data": []}) == []


def test_sbir_cache_rejects_placeholder_rows_but_accepts_empty(tmp_path):
    placeholder = tmp_path / "open_2026-07-20.json"
    placeholder.write_text(json.dumps({
        "retrieved_at": "2026-07-20T12:00:00+00:00",
        "records": [{"message": "maintenance"}],
    }))
    with pytest.raises(ValueError, match="omitted number or title"):
        sb._read_cache(placeholder)

    empty = tmp_path / "open_2026-07-21.json"
    empty.write_text(json.dumps({
        "retrieved_at": "2026-07-21T12:00:00+00:00",
        "records": [],
    }))
    assert sb._read_cache(empty)[0] == []


def test_sbir_program_projection_flattens_exact_topic_narrative(monkeypatch, tmp_path):
    from datetime import datetime, timezone

    monkeypatch.setenv("LILA_SBIR_CACHE_DIR", str(tmp_path))
    fixed = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(sb, "_utc_now", lambda: fixed)
    monkeypatch.setattr(sb, "get_json", lambda *args, **kwargs: [{
        "solicitation_title": "Federal innovation omnibus",
        "solicitation_number": "SBIR-26-1",
        "agency": "HHS",
        "open_date": "2026-07-21",
        "close_date": "2026-10-01",
        "solicitation_topics": [{
            "topic_code": "A-1",
            "topic_title": "Responder network telemetry",
            "topic_description": (
                "Network telemetry for civilian emergency responders."
            ),
            "sbir_topic_link": "https://www.sbir.gov/topics/9001",
        }, {
            "topic_code": "A-2",
            "topic_title": "Unrelated materials science",
            "topic_description": "Novel polymer research.",
            "sbir_topic_link": "https://www.sbir.gov/topics/9002",
        }],
    }])

    out = SbirSource().enrich(SourceQuery(keywords=["network telemetry"]))

    assert [row["record_id"] for row in out["records"]] == [
        "sbir_gov:SBIR-26-1:A-1",
        "sbir_gov:SBIR-26-1:A-2",
    ]
    assert out["records"][0]["objective"] == (
        "Network telemetry for civilian emergency responders."
    )
    assert out["records"][0]["canonical_url"] == (
        "https://www.sbir.gov/topics/9001"
    )
    assert out["records"][1]["matched_terms"] == ["network telemetry"]
    assert out["_provenance"]["records_matched"] == 2


def test_sbir_all_open_pull_is_shared_across_keyword_lanes(monkeypatch, tmp_path):
    monkeypatch.setenv("LILA_SBIR_CACHE_DIR", str(tmp_path))
    calls = []

    def fake_get(url, *, params, **kw):
        calls.append(params)
        return [{
            "solicitation_title": "Resilient operations",
            "solicitation_number": "DOD-26-1",
            "agency": "DOD",
            "solicitation_topics": [{
                "topic_title": "Zero trust command network",
                "topic_description": "Cyber defense for distributed systems",
            }],
        }]

    monkeypatch.setattr(sb, "get_json", fake_get)
    out = SbirSource().enrich(SourceQuery(keywords=["zero trust", "cyber"]))

    assert len(calls) == 1
    assert calls[0] == {"open": 1, "rows": 50, "start": 0}
    assert out["zero trust"][0]["number"] == "DOD-26-1"
    assert out["cyber"][0]["matched"] == "cyber"


def test_sbir_screens_terms_beyond_the_former_first_five(monkeypatch, tmp_path):
    monkeypatch.setenv("LILA_SBIR_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(sb, "get_json", lambda *args, **kwargs: [{
        "solicitation_title": "Resilient optical switching",
        "solicitation_number": "DOD-26-6",
        "agency": "DOD",
        "solicitation_topics": [{
            "topic_code": "N-6",
            "topic_title": "Resilient optical switching",
            "topic_description": "Network fabric research.",
            "sbir_topic_link": "https://www.sbir.gov/topics/n-6",
        }],
    }])
    terms = ["alpha", "bravo", "charlie", "delta", "echo",
             "optical switching"]

    out = SbirSource().enrich(SourceQuery(keywords=terms))

    assert out["optical switching"][0]["number"] == "DOD-26-6"
    assert out["records"][0]["matched_terms"] == ["optical switching"]


def test_sbir_429_uses_dated_official_cache_and_marks_every_row_stale(
    monkeypatch, tmp_path
):
    from datetime import datetime, timedelta, timezone
    import json

    monkeypatch.setenv("LILA_SBIR_CACHE_DIR", str(tmp_path))
    fixed = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(sb, "_utc_now", lambda: fixed)
    snapshot_day = fixed.date() - timedelta(days=2)
    (tmp_path / f"open_{snapshot_day.isoformat()}.json").write_text(json.dumps({
        "retrieved_at": f"{snapshot_day.isoformat()}T12:00:00+00:00",
        "records": [{
            "solicitation_title": "Cyber mission analytics",
            "solicitation_number": "CACHE-1",
            "agency": "DHS",
            "solicitation_topics": [],
        }],
    }))

    def rate_limited(*args, **kwargs):
        raise RuntimeError("HTTP 429 — maintenance")

    monkeypatch.setattr(sb, "get_json", rate_limited)
    monkeypatch.setattr(sb, "get_text", lambda *args, **kwargs: (
        (_ for _ in ()).throw(RuntimeError("Topics listing unavailable"))
    ))
    out = SbirSource().enrich(SourceQuery(keywords=["cyber"]))

    assert out["_provenance"]["mode"] == "stale_official_cache"
    assert out["_provenance"]["age_days"] == 2
    assert out["_provenance"]["source_attempts"][0]["status"] == "failed"
    assert out["cyber"][0]["freshness"] == "stale"
    assert out["cyber"][0]["status_as_of"].startswith(snapshot_day.isoformat())


def _topic_html(rows):
    cards = []
    for topic_id, title, description in rows:
        cards.append(f"""
          <h3><a href="/topics/{topic_id}">{title}</a></h3>
          <p class="margin-bottom-205"><span>Open</span>
            <b>Release Date:</b> July 1, 2026
            <span><b>Open Date:</b> July 22, 2026</span>
            <span><b>Close Date:</b> August 19, 2026</span>
          </p>
          <div><img alt="Seal of the Agency: DOD" src="seal.png">
            <p class="measure-6">{description}</p>
          </div>
        """)
    return "<html><body>" + "".join(cards) + "</body></html>"


def test_sbir_429_first_run_uses_current_official_topics_listing(
    monkeypatch, tmp_path
):
    import json

    monkeypatch.setenv("LILA_SBIR_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(sb, "get_json", lambda *args, **kwargs: (
        (_ for _ in ()).throw(RuntimeError("HTTP 429 — API maintenance"))
    ))
    monkeypatch.setattr(sb, "get_text", lambda *args, **kwargs: _topic_html([
        ("12564", "Assured command systems", "Zero trust cyber operations"),
    ]))

    out = SbirSource().enrich(SourceQuery(keywords=["zero trust"]))

    assert out["_provenance"]["mode"] == "live_official_topics_listing"
    assert out["_provenance"]["stale"] is False
    assert "verify the linked agency" in out["_provenance"]["limitations"]
    assert [a["status"] for a in out["_provenance"]["source_attempts"]] == [
        "failed", "success"
    ]
    row = out["zero trust"][0]
    assert row["number"] == "12564"
    assert row["agency"] == "DOD"
    assert row["close"] == "2026-08-19"
    assert row["url"] == "https://www.sbir.gov/topics/12564"
    assert row["freshness"] == "current_official_listing"
    cached = json.loads(next(tmp_path.glob("open_*.json")).read_text())
    assert cached["source"] == "https://www.sbir.gov/topics"


def test_sbir_healthcheck_accepts_official_topics_continuity(monkeypatch):
    monkeypatch.setattr(sb, "get_json", lambda *args, **kwargs: (
        (_ for _ in ()).throw(RuntimeError("HTTP 429 — API maintenance"))
    ))
    monkeypatch.setattr(sb, "get_text", lambda *args, **kwargs: _topic_html([
        ("12564", "Assured command systems", "Zero trust cyber operations"),
    ]))

    ok, detail = SbirSource().healthcheck()

    assert ok is True
    assert "official Topics listing reachable" in detail


def test_sbir_topics_listing_paginates_until_short_page(monkeypatch):
    pages = []

    def fake_text(url, **kwargs):
        pages.append(url)
        page = int(url.rsplit("page=", 1)[-1])
        count = 10 if page == 0 else 1
        return _topic_html([
            (f"{page}-{i}", f"Topic {page}-{i}", "Cyber mission")
            for i in range(count)
        ])

    monkeypatch.setattr(sb, "get_text", fake_text)
    rows = sb._fetch_topics_listing()

    assert len(rows) == 11
    assert [url.rsplit("page=", 1)[-1] for url in pages] == ["0", "1"]


def test_sbir_api_page_cap_is_named_partial(monkeypatch, tmp_path):
    monkeypatch.setenv("LILA_SBIR_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(sb, "MAX_PAGES", 2)
    calls = []

    def full_page(_url, *, params, **_kwargs):
        calls.append(params["start"])
        return [
            {
                "solicitation_title": f"Topic {params['start'] + index}",
                "solicitation_number": f"SBIR-{params['start'] + index}",
                "agency": "HHS",
                "solicitation_topics": [],
            }
            for index in range(sb.PAGE_SIZE)
        ]

    monkeypatch.setattr(sb, "get_json", full_page)
    _records, provenance = sb._fetch_open()

    assert calls == [0, 50]
    assert provenance["status"] == "partial"
    assert provenance["partial"] is True
    assert provenance["source_attempts"][-1]["status"] == "partial"
    assert "paging capped at 100 rows" in provenance["limitations"]


def test_sbir_topics_page_cap_is_named_partial(monkeypatch):
    monkeypatch.setattr(sb, "MAX_TOPIC_PAGES", 1)
    monkeypatch.setattr(sb, "get_text", lambda *_args, **_kwargs: _topic_html([
        (f"cap-{index}", f"Topic {index}", "Cyber mission")
        for index in range(10)
    ]))

    rows, partial = sb._fetch_topics_listing_snapshot()

    assert len(rows) == 10
    assert partial is True


def test_sbir_daily_cache_uses_one_utc_day_across_local_midnight(
    monkeypatch, tmp_path
):
    from datetime import datetime, timezone

    monkeypatch.setenv("LILA_SBIR_CACHE_DIR", str(tmp_path))
    fixed = datetime(2026, 7, 21, 0, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(sb, "_utc_now", lambda: fixed)
    calls = []

    def live(*args, **kwargs):
        calls.append(1)
        return [{
            "solicitation_title": "Cyber continuity",
            "solicitation_number": "UTC-1",
            "agency": "DHS",
            "solicitation_topics": [],
        }]

    monkeypatch.setattr(sb, "get_json", live)
    first = SbirSource().enrich(SourceQuery(keywords=["cyber"]))
    second = SbirSource().enrich(SourceQuery(keywords=["cyber"]))

    assert len(calls) == 1
    assert (tmp_path / "open_2026-07-21.json").exists()
    assert first["_provenance"]["mode"] == "live_official_api"
    assert second["_provenance"]["mode"] == "official_daily_cache"


def test_sbir_legacy_current_cache_retries_and_rewrites_completeness(
    monkeypatch, tmp_path
):
    from datetime import datetime, timezone
    import json

    monkeypatch.setenv("LILA_SBIR_CACHE_DIR", str(tmp_path))
    fixed = datetime(2026, 7, 21, 1, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(sb, "_utc_now", lambda: fixed)
    current = tmp_path / "open_2026-07-21.json"
    current.write_text(json.dumps({
        "retrieved_at": "2026-07-21T00:15:13+00:00",
        "origin": "topics_listing",
        "records": [{
            "solicitation_title": "Legacy cached topic",
            "solicitation_number": "LEGACY-1",
            "agency": "DOD",
            "solicitation_topics": [],
        }],
    }))
    monkeypatch.setattr(sb, "get_json", lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(RuntimeError("HTTP 429 - maintenance"))
    ))
    monkeypatch.setattr(sb, "get_text", lambda *_args, **_kwargs: _topic_html([
        ("12564", "Assured command systems", "Zero trust cyber operations"),
    ]))

    out = SbirSource().enrich(SourceQuery(keywords=["zero trust"]))

    assert out["_provenance"]["mode"] == "live_official_topics_listing"
    assert out["_provenance"]["source_attempts"][0] == {
        "source": "daily_cache", "status": "partial",
    }
    rewritten = json.loads(current.read_text())
    assert rewritten["partial"] is False
    assert rewritten["origin"] == "topics_listing"
    assert out["zero trust"][0]["number"] == "12564"


def test_sbir_failed_refresh_retains_named_partial_current_cache(
    monkeypatch, tmp_path
):
    from datetime import datetime, timezone
    import json

    monkeypatch.setenv("LILA_SBIR_CACHE_DIR", str(tmp_path))
    fixed = datetime(2026, 7, 21, 1, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(sb, "_utc_now", lambda: fixed)
    current = tmp_path / "open_2026-07-21.json"
    current.write_text(json.dumps({
        "retrieved_at": "2026-07-21T00:15:13+00:00",
        "origin": "topics_listing",
        "partial": True,
        "limitations": "official Topics listing paging capped at 20 pages",
        "records": [{
            "solicitation_title": "Cyber recovery",
            "solicitation_number": "CACHE-CURRENT",
            "agency": "DHS",
            "solicitation_agency_url": "https://www.sbir.gov/topics/current",
            "solicitation_topics": [],
        }],
    }))
    monkeypatch.setattr(sb, "get_json", lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(RuntimeError("API down"))
    ))
    monkeypatch.setattr(sb, "get_text", lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(RuntimeError("listing down"))
    ))

    out = SbirSource().enrich(SourceQuery(keywords=["cyber"]))

    provenance = out["_provenance"]
    assert provenance["mode"] == "partial_current_day_cache"
    assert provenance["freshness"] == "current_day_snapshot"
    assert provenance["stale"] is False
    assert provenance["status"] == "partial"
    assert "API=API down; listing=listing down" in provenance["live_error"]
    assert provenance["source_attempts"][0] == {
        "source": "daily_cache", "status": "partial",
    }
    assert provenance["source_attempts"][-1] == {
        "source": "current_day_official_cache", "status": "partial",
    }
    assert out["cyber"][0]["number"] == "CACHE-CURRENT"
    assert out["cyber"][0]["freshness"] == "current_day_snapshot"


def test_sbir_invalid_current_cache_does_not_block_older_official_snapshot(
    monkeypatch, tmp_path
):
    from datetime import datetime, timezone
    import json

    monkeypatch.setenv("LILA_SBIR_CACHE_DIR", str(tmp_path))
    fixed = datetime(2026, 7, 21, 1, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(sb, "_utc_now", lambda: fixed)
    (tmp_path / "open_2026-07-21.json").write_text("{not-json")
    (tmp_path / "open_2026-07-20.json").write_text(json.dumps({
        "retrieved_at": "2026-07-20T12:00:00+00:00",
        "origin": "topics_listing",
        "records": [{
            "solicitation_title": "Cyber recovery",
            "solicitation_number": "CACHE-2",
            "agency": "DHS",
            "solicitation_topics": [],
        }],
    }))
    monkeypatch.setattr(sb, "get_json", lambda *args, **kwargs: (
        (_ for _ in ()).throw(RuntimeError("API down"))))
    monkeypatch.setattr(sb, "get_text", lambda *args, **kwargs: (
        (_ for _ in ()).throw(RuntimeError("listing down"))))

    out = SbirSource().enrich(SourceQuery(keywords=["cyber"]))

    assert out["_provenance"]["mode"] == "stale_official_cache"
    assert out["_provenance"]["age_days"] == 1
    assert out["_provenance"]["source_attempts"][0]["source"] == "daily_cache"
    assert out["_provenance"]["source_attempts"][0]["status"] == "failed"
    assert out["cyber"][0]["number"] == "CACHE-2"


def test_gdelt_drops_urlless_articles(monkeypatch, tmp_path):
    monkeypatch.setenv("LILA_GDELT_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(gd, "get_json", lambda url, *, params, **kw: {
        "articles": [{"title": "no url", "domain": "x.com"},
                     {"title": "DHS expands CTI", "url": "https://example.com/a",
                      "seendate": "20260701T120000Z", "domain": "example.com"}]})
    out = GdeltSource().enrich(SourceQuery(keywords=["threat intelligence"]))
    arts = out["threat intelligence"]
    assert len(arts) == 1 and arts[0]["url"] == "https://example.com/a"


def test_gdelt_quotes_multiword_terms(monkeypatch, tmp_path):
    monkeypatch.setenv("LILA_GDELT_CACHE_DIR", str(tmp_path))
    seen = {}

    def spy(url, *, params, **kw):
        seen["q"] = params["query"]
        return {"articles": []}

    monkeypatch.setattr(gd, "get_json", spy)
    GdeltSource().enrich(SourceQuery(keywords=["threat intelligence"]))
    assert seen["q"] == '"threat intelligence"'


def test_gdelt_does_not_cache_error_object_as_complete(monkeypatch, tmp_path):
    monkeypatch.setenv("LILA_GDELT_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(gd, "_pace_request", lambda root: None)
    monkeypatch.setattr(
        gd,
        "get_json",
        lambda *args, **kwargs: {"message": "rate limit exceeded"},
    )

    with pytest.raises(RuntimeError, match="no articles list"):
        GdeltSource().enrich(SourceQuery(keywords=["cybersecurity"]))

    assert not list(tmp_path.glob("snapshot_*.json"))


def test_gdelt_paces_live_lanes_and_reuses_current_day_cache(
    monkeypatch, tmp_path
):
    from datetime import datetime, timezone

    monkeypatch.setenv("LILA_GDELT_CACHE_DIR", str(tmp_path))
    fixed_now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(gd, "_utc_now", lambda: fixed_now)
    clock = [1_000.0]
    sleeps = []

    monkeypatch.setattr(gd, "_wall_time", lambda: clock[0])

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(gd, "_sleep", fake_sleep)
    calls = []

    def fake_get(url, *, params, **kwargs):
        calls.append((params["query"], kwargs.get("retries")))
        return {
            "articles": [
                {
                    "title": f"Article for {params['query']}",
                    "url": f"https://example.com/{len(calls)}",
                }
            ]
        }

    monkeypatch.setattr(gd, "get_json", fake_get)
    source_query = SourceQuery(keywords=["cybersecurity", "network modernization"])

    first = GdeltSource().enrich(source_query)
    second = GdeltSource().enrich(source_query)

    assert calls == [("cybersecurity", 1), ('"network modernization"', 1)]
    assert sleeps == [gd.MIN_REQUEST_INTERVAL_S]
    assert first["_provenance"]["mode"] == "live_public_api"
    assert first["_provenance"]["status"] == "complete"
    assert second["_provenance"]["mode"] == "current_day_cache"
    assert second["_provenance"]["retrieval_mode"] == "stored"
    assert second["network modernization"][0]["url"] == "https://example.com/2"


def test_gdelt_cache_preserves_the_snapshot_retrieval_time(
    monkeypatch, tmp_path
):
    from datetime import datetime, timezone

    monkeypatch.setenv("LILA_GDELT_CACHE_DIR", str(tmp_path))
    now = datetime(2026, 7, 20, 18, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(gd, "_utc_now", lambda: now)
    query = "cybersecurity"
    cached_at = "2026-07-20T08:15:00+00:00"
    gd._write_current_cache(
        gd._cache_path(tmp_path, query, "2026-07-20"),
        query=query,
        retrieved_at=cached_at,
        articles=[],
    )

    out = GdeltSource().enrich(SourceQuery(keywords=[query]))

    assert out["_provenance"]["mode"] == "current_day_cache"
    assert out["_provenance"]["retrieved_at"] == "2026-07-20T08:15:00Z"


def test_gdelt_partial_provenance_names_failed_and_omitted_lanes(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("LILA_GDELT_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(gd, "_pace_request", lambda root: None)
    calls = []

    def fake_get(url, *, params, **kwargs):
        calls.append(params["query"])
        if len(calls) == 2:
            raise RuntimeError("HTTP 429 — one request every five seconds")
        return {
            "articles": [
                {
                    "title": f"Article for {params['query']}",
                    "url": f"https://example.com/{len(calls)}",
                }
            ]
        }

    monkeypatch.setattr(gd, "get_json", fake_get)
    keywords = ["one", "two", "three", "four", "five", "six", "seven"]
    out = GdeltSource().enrich(SourceQuery(keywords=keywords))
    provenance = out["_provenance"]

    assert provenance["status"] == "partial"
    assert provenance["record_count"] == 4
    assert "4 of 5 attempted" in provenance["public_detail"]
    assert "2 requested lanes omitted" in provenance["public_detail"]
    failed = [row for row in provenance["attempts"] if row["status"] == "failed"]
    omitted = [row for row in provenance["attempts"] if row["status"] == "not-run"]
    assert failed == [
        {
            "source": "gdelt_live:two",
            "status": "failed",
            "error": "HTTP 429 — one request every five seconds",
        }
    ]
    assert [row["source"] for row in omitted] == [
        "gdelt_not_run:six",
        "gdelt_not_run:seven",
    ]
    assert any("successfully" in value for value in provenance["limitations"])
    assert any("2 requested" in value for value in provenance["limitations"])


def test_gdelt_all_lane_failure_is_not_hidden(monkeypatch, tmp_path):
    monkeypatch.setenv("LILA_GDELT_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(gd, "_pace_request", lambda root: None)
    monkeypatch.setattr(
        gd,
        "get_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("HTTP 429 — pace requests")
        ),
    )

    with pytest.raises(RuntimeError, match="failed for all 1.*HTTP 429"):
        GdeltSource().enrich(SourceQuery(keywords=["cybersecurity"]))


def test_distiller_and_provenance_cover_new_lanes():
    from agents.decisions.research_picture import distill, render_provenance
    sweep = distill({
        "sam.gov": [{"source_id": "N-1", "title": "t"}],
        "triage": {"N-1": {"verdict": "pursue", "reason": "r"}},
        "grants_gov": {"cyber": [{"title": "G", "agency": "DHS", "status": "posted"}]},
        "sbir_gov": {"cyber": [{"title": "S", "agency": "DOD"}]},
        "gdelt": {"cyber": [{"title": "A", "url": "https://x.com", "domain": "x.com"}]},
        "cisa_kev": {"recent_count": 7, "window_days": 30, "matched": []},
    })
    assert sweep["grant_programs"][0]["title"] == "G"
    assert sweep["sbir_solicitations"][0]["title"] == "S"
    assert sweep["global_news"][0]["title"] == "A"
    prov = render_provenance(sweep)
    for needle in ("1 SAM.gov notices", "1 pursue-grade", "1 grant programs",
                   "1 SBIR/STTR topics", "1 global news articles",
                   "7 exploited vulns", "not verified opportunities"):  # bounded provenance wording
        assert needle in prov, needle


def test_provenance_empty_sweep_renders_nothing():
    from agents.decisions.research_picture import render_provenance
    assert render_provenance({}) == ""
