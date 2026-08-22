"""Recorded contracts for four official PROGRAM-tier source adapters."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import tools.api.darpa_opportunities as darpa
import tools.api.dod_budget_exhibits as dod_budget
import tools.api.dsip_topics as dsip
import tools.api.foreign_assistance as foreign
import tools.api.program_cache as program_cache
import tools.api.reginfo_unified_agenda as reginfo
from tools.api.base import SourceKind, SourceQuery
from tools.api.darpa_opportunities import DarpaOpportunitiesSource
from tools.api.dod_budget_exhibits import DodBudgetExhibitsSource
from tools.api.dsip_topics import DsipTopicsSource
from tools.api.foreign_assistance import ForeignAssistanceSource
from tools.api.reginfo_unified_agenda import RegInfoUnifiedAgendaSource

FIXTURES = Path(__file__).parent / "fixtures"
FIXED_NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _fixed_clock(monkeypatch) -> None:
    monkeypatch.setattr(program_cache, "_utc_now", lambda: FIXED_NOW)


def test_reginfo_recorded_xml_maps_program_fields(monkeypatch, tmp_path):
    _fixed_clock(monkeypatch)
    monkeypatch.setenv("LILA_REGINFO_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("LILA_REGINFO_XML_URL", "https://reginfo.example/current.xml")
    monkeypatch.setattr(reginfo, "get_text", lambda *args, **kwargs: _fixture("reginfo_unified_agenda.xml"))

    out = RegInfoUnifiedAgendaSource().enrich(
        SourceQuery(keywords=["zero trust"], agencies=["Defense"])
    )

    assert out["_provenance"]["status"] == "success"
    assert out["_provenance"]["promotion_eligible"] is False
    assert out["_provenance"]["data_as_of"] == "2026-07-03"
    assert len(out["records"]) == 1
    row = out["records"][0]
    assert row["record_id"] == "reginfo:0704-AI01:202510"
    assert row["canonical_url"].endswith("RIN=0704-AI01&pubId=202510")
    assert row["tier"] == "program"
    assert row["source"] == "reginfo_unified_agenda"
    assert row["retrieved_at"] == "2026-07-20T12:00:00+00:00"
    assert row["data_as_of"] == "2026-07-03"
    assert row["abstract"] == "Establishes zero trust reporting and data-exchange requirements."
    assert row["timetable"][0] == {
        "action": "NPRM",
        "date": "11/00/2026",
        "federal_register_citation": "91 FR 1000",
    }


def test_reginfo_rejects_dated_but_empty_xml_snapshot():
    with pytest.raises(ValueError, match="no parseable agenda records"):
        reginfo._parse_xml('<REGINFO RUN_DATE="2026-08-14" />')


def test_darpa_recorded_rss_uses_item_canonical_link(monkeypatch, tmp_path):
    _fixed_clock(monkeypatch)
    monkeypatch.setenv("LILA_DARPA_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(darpa, "get_text", lambda *args, **kwargs: _fixture("darpa_opportunities.xml"))

    out = DarpaOpportunitiesSource().enrich(SourceQuery(keywords=["cold-atom"]))

    assert out["_provenance"]["data_as_of"] == "2026-07-16"
    assert len(out["records"]) == 1
    row = out["records"][0]
    assert row["record_id"] == "darpa:5240"
    assert row["canonical_url"] == "https://www.darpa.mil/research/programs/perrseus"
    assert row["signal_type"] == "research_program"
    assert row["tier"] == "program"
    assert row["published_at"] == "2026-07-16T15:22:22+00:00"


def test_darpa_undated_feed_is_rejected_instead_of_stamped_today():
    xml = (
        "<rss><channel><item><title>Network research program</title>"
        "<guid>program-1</guid><link>https://www.darpa.mil/program-1</link>"
        "</item></channel></rss>"
    )

    with pytest.raises(ValueError, match="no item or channel publication date"):
        darpa._parse_rss(xml)


def test_dod_recorded_json_is_partial_program_coverage(monkeypatch, tmp_path):
    _fixed_clock(monkeypatch)
    monkeypatch.setenv("LILA_DOD_BUDGET_CACHE_DIR", str(tmp_path))
    payload = json.loads(_fixture("dod_budget_exhibit.json"))

    def fake_get(url, **kwargs):
        if "Drug_Interdiction" in url:
            raise RuntimeError("recorded secondary book outage")
        return payload

    monkeypatch.setattr(dod_budget, "get_json", fake_get)
    out = DodBudgetExhibitsSource().enrich(SourceQuery(keywords=["resilient"]))

    assert out["_provenance"]["status"] == "partial"
    assert out["_provenance"]["partial"] is True
    assert [attempt["status"] for attempt in out["_provenance"]["source_attempts"]] == [
        "success",
        "failed",
    ]
    assert len(out["records"]) == 1
    row = out["records"][0]
    assert row["tier"] == "program"
    assert row["data_as_of"] == "2026-04"
    assert row["values"]["TOTAL"] == 2_906_393
    assert row["record_id"].endswith(":PDI_FundTypeByCate:PDITab4Loe:PDITYP_Loe1")
    assert "Grand Total" not in row["title"]


def test_foreign_assistance_paginates_and_keeps_latest_fiscal_year(
    monkeypatch, tmp_path
):
    _fixed_clock(monkeypatch)
    monkeypatch.setenv("LILA_FOREIGN_ASSISTANCE_CACHE_DIR", str(tmp_path))
    pages = {
        1: json.loads(_fixture("foreign_assistance_page_1.json")),
        2: json.loads(_fixture("foreign_assistance_page_2.json")),
    }
    calls = []

    def fake_get(url, *, params, **kwargs):
        calls.append(dict(params))
        return pages[params["page"]]

    monkeypatch.setattr(foreign, "get_json", fake_get)
    out = ForeignAssistanceSource().enrich(SourceQuery(keywords=["cybersecurity"]))

    assert [call["page"] for call in calls] == [1, 2]
    assert all(call["transaction_type_id"] == 18 for call in calls)
    assert out["_provenance"]["status"] == "success"
    assert out["_provenance"]["data_as_of"] == "FY2024"
    assert len(out["records"]) == 1
    row = out["records"][0]
    assert row["record_id"] == "foreign-assistance:102"
    assert row["current_amount"] == 2_500_000
    assert row["agency"] == "Department of State"
    assert row["funding_account"] == "Cybersecurity Capacity Building"
    assert row["canonical_url"] == "https://foreignassistance.gov/data"
    assert row["tier"] == "program"


def test_foreign_assistance_requires_pagination_metadata(monkeypatch, tmp_path):
    _fixed_clock(monkeypatch)
    monkeypatch.setenv("LILA_FOREIGN_ASSISTANCE_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(
        foreign,
        "get_json",
        lambda *args, **kwargs: {
            "data": [{
                "id": 1,
                "fiscal_year": 2026,
                "funding_agency_name": "Department of State",
            }]
        },
    )

    with pytest.raises(ValueError, match="no page_info object"):
        foreign._fetch_live()


def test_foreign_assistance_rejects_pagination_drift(monkeypatch, tmp_path):
    _fixed_clock(monkeypatch)
    monkeypatch.setenv("LILA_FOREIGN_ASSISTANCE_CACHE_DIR", str(tmp_path))

    def fake_get(url, *, params, **kwargs):
        page = params["page"]
        return {
            "data": [{
                "id": page,
                "fiscal_year": 2026,
                "funding_agency_name": "Department of State",
            }],
            "page_info": {
                "current_page": page,
                "total_pages": 2 if page == 1 else 3,
            },
        }

    monkeypatch.setattr(foreign, "get_json", fake_get)
    with pytest.raises(ValueError, match="changed during pagination"):
        foreign._fetch_live()


def test_foreign_assistance_accepts_explicit_empty_container_metadata():
    assert foreign._rows({"data": []}) == []
    assert foreign._total_pages(
        {"page_info": {"current_page": 1, "total_pages": 1}},
        expected_page=1,
    ) == 1


def test_foreign_assistance_default_census_completes_beyond_five_pages(
    monkeypatch, tmp_path
):
    _fixed_clock(monkeypatch)
    monkeypatch.setenv("LILA_FOREIGN_ASSISTANCE_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(foreign, "MAX_PAGES", foreign.DEFAULT_MAX_PAGES)
    calls = []
    total_pages = 7

    def fake_get(url, *, params, **kwargs):
        page = params["page"]
        calls.append(page)
        return {
            "data": [
                {
                    "id": 1_000 + page,
                    "fiscal_year": 2026,
                    "funding_agency_name": "Department of State",
                    "funding_account_name": f"Cybersecurity program {page}",
                    "country_name": "Global",
                    "current_amount": page * 1_000,
                }
            ],
            "page_info": {"total_pages": total_pages},
        }

    monkeypatch.setattr(foreign, "get_json", fake_get)
    out = ForeignAssistanceSource().enrich(
        SourceQuery(keywords=["cybersecurity"], limit=20)
    )

    assert foreign.DEFAULT_MAX_PAGES >= 9
    assert calls == list(range(1, total_pages + 1))
    assert out["_provenance"]["status"] == "success"
    assert out["_provenance"]["partial"] is False
    assert len(out["records"]) == total_pages
    assert "capped" not in out["_provenance"]["limitations"]


def test_foreign_assistance_scopes_agency_before_value_cap(monkeypatch, tmp_path):
    _fixed_clock(monkeypatch)
    monkeypatch.setenv("LILA_FOREIGN_ASSISTANCE_CACHE_DIR", str(tmp_path))

    def fake_get(url, *, params, **kwargs):
        return {
            "data": [
                {
                    "id": 1,
                    "fiscal_year": 2026,
                    "funding_agency_name": "Department of Defense",
                    "funding_account_name": "Cybersecurity platform",
                    "current_amount": 9_000_000,
                },
                {
                    "id": 2,
                    "fiscal_year": 2026,
                    "funding_agency_name": "Department of State",
                    "funding_account_name": "Cybersecurity platform",
                    "current_amount": 1_000_000,
                },
            ],
            "page_info": {"total_pages": 1},
        }

    monkeypatch.setattr(foreign, "get_json", fake_get)
    out = ForeignAssistanceSource().enrich(
        SourceQuery(
            keywords=["cybersecurity"],
            agencies=["Department of State"],
            limit=1,
        )
    )

    assert [row["agency"] for row in out["records"]] == [
        "Department of State"
    ]
    assert out["_provenance"]["candidate_total"] == 1


def test_daily_cache_is_reused_and_atomically_complete(monkeypatch, tmp_path):
    _fixed_clock(monkeypatch)
    monkeypatch.setenv("LILA_DARPA_CACHE_DIR", str(tmp_path))
    calls = []

    def fake_get(*args, **kwargs):
        calls.append(1)
        return _fixture("darpa_opportunities.xml")

    monkeypatch.setattr(darpa, "get_text", fake_get)
    first = DarpaOpportunitiesSource().enrich(SourceQuery())
    second = DarpaOpportunitiesSource().enrich(SourceQuery())

    assert len(calls) == 1
    assert first["_provenance"]["mode"] == "live_official_source"
    assert second["_provenance"]["mode"] == "official_daily_cache"
    snapshot = tmp_path / "snapshot_2026-07-20.json"
    assert json.loads(snapshot.read_text(encoding="utf-8"))["records"]
    assert not [path for path in tmp_path.iterdir() if path.name.endswith(".tmp")]


@pytest.mark.parametrize(
    ("source_class", "module", "cache_env", "http_name", "canonical_url"),
    [
        (RegInfoUnifiedAgendaSource, reginfo, "LILA_REGINFO_CACHE_DIR",
         "get_text", reginfo.INDEX_URL),
        (DarpaOpportunitiesSource, darpa, "LILA_DARPA_CACHE_DIR",
         "get_text", darpa.FEED_URL),
        (DodBudgetExhibitsSource, dod_budget, "LILA_DOD_BUDGET_CACHE_DIR",
         "get_json", dod_budget.INDEX_URL),
        (
            ForeignAssistanceSource,
            foreign,
            "LILA_FOREIGN_ASSISTANCE_CACHE_DIR",
            "get_json",
            foreign.DATA_URL,
        ),
    ],
    ids=["reginfo", "darpa", "dod-budget", "foreign-assistance"],
)
def test_corrupt_current_cache_falls_back_with_named_stale_provenance(
    monkeypatch, tmp_path, source_class, module, cache_env, http_name,
    canonical_url,
):
    _fixed_clock(monkeypatch)
    monkeypatch.setenv(cache_env, str(tmp_path))
    monkeypatch.setenv("LILA_REGINFO_XML_URL", "https://reginfo.example/current.xml")
    (tmp_path / "snapshot_2026-07-20.json").write_text("{not-json", encoding="utf-8")
    source_name = source_class.name
    old_record = {
        "record_id": f"{source_name}:cached",
        "canonical_url": "https://example.gov/program/cached",
        "source": source_name,
        "tier": "program",
        "title": "Cached official program signal",
        "retrieved_at": "2026-07-19T12:00:00+00:00",
        "data_as_of": "2026-07-18",
    }
    (tmp_path / "snapshot_2026-07-19.json").write_text(
        json.dumps(
            {
                "source": source_name,
                "canonical_url": canonical_url,
                "retrieved_at": "2026-07-19T12:00:00+00:00",
                "data_as_of": "2026-07-18",
                "partial": False,
                "records": [old_record],
            }
        ),
        encoding="utf-8",
    )

    def down(*args, **kwargs):
        raise RuntimeError("official source unavailable")

    monkeypatch.setattr(module, http_name, down)
    out = source_class().enrich(SourceQuery(limit=1))

    provenance = out["_provenance"]
    assert out["records"] == [old_record]
    assert provenance["mode"] == "stale_official_cache"
    assert provenance["status"] == "partial"
    assert provenance["stale"] is True
    assert provenance["age_days"] == 1
    assert provenance["source_attempts"][0]["source"] == "daily_cache"
    assert provenance["source_attempts"][0]["status"] == "failed"
    assert "official source unavailable" in provenance["live_error"]


def test_partial_current_cache_retries_and_persists_child_lineage(
    monkeypatch, tmp_path
):
    _fixed_clock(monkeypatch)
    source = "reginfo_unified_agenda"
    canonical = reginfo.INDEX_URL
    cached_record = {
        "record_id": "reginfo:cached",
        "canonical_url": "https://www.reginfo.gov/cached",
        "source": source,
        "tier": "program",
        "agency": "Department of Transportation",
        "retrieved_at": "2026-07-20T08:00:00+00:00",
        "data_as_of": "2026-06-30",
    }
    snapshot = tmp_path / "snapshot_2026-07-20.json"
    snapshot.write_text(json.dumps({
        "source": source,
        "canonical_url": canonical,
        "retrieved_at": "2026-07-20T08:00:00+00:00",
        "data_as_of": "2026-06-30",
        "partial": True,
        "source_attempts": [{
            "source": "agenda_xml", "status": "failed",
            "error": "transient child outage",
        }],
        "records": [cached_record],
    }), encoding="utf-8")
    calls = []

    def recovered():
        calls.append(1)
        return program_cache.ProgramPull(
            [{
                "record_id": "reginfo:fresh",
                "canonical_url": "https://www.reginfo.gov/fresh",
                "agency": "Department of Transportation",
            }],
            "2026-07-20",
            source_attempts=[{
                "source": "agenda_xml", "status": "success",
            }],
        )

    rows, provenance = program_cache.cached_program_pull(
        source=source,
        canonical_url=canonical,
        cache_dir=tmp_path,
        fetch_live=recovered,
    )

    assert calls == [1]
    assert [row["record_id"] for row in rows] == ["reginfo:fresh"]
    assert provenance["status"] == "success"
    assert [row["status"] for row in provenance["source_attempts"]] == [
        "failed", "partial", "success",
    ]
    persisted = json.loads(snapshot.read_text(encoding="utf-8"))
    assert persisted["source_attempts"] == [{
        "source": "agenda_xml", "status": "success",
    }]


def test_cache_reader_rejects_cross_source_snapshot(tmp_path):
    path = tmp_path / "snapshot_2026-07-20.json"
    path.write_text(json.dumps({
        "source": "darpa_opportunities",
        "canonical_url": darpa.FEED_URL,
        "retrieved_at": "2026-07-20T08:00:00+00:00",
        "data_as_of": "2026-07-20",
        "records": [{
            "record_id": "darpa:wrong-cache",
            "canonical_url": "https://www.darpa.mil/wrong-cache",
            "source": "darpa_opportunities",
            "tier": "program",
            "retrieved_at": "2026-07-20T08:00:00+00:00",
            "data_as_of": "2026-07-20",
        }],
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match"):
        program_cache._read_snapshot(
            path,
            expected_source="reginfo_unified_agenda",
            expected_canonical_url=reginfo.INDEX_URL,
        )


@pytest.mark.parametrize(
    ("source_class", "module"),
    [
        (RegInfoUnifiedAgendaSource, reginfo),
        (DarpaOpportunitiesSource, darpa),
        (DodBudgetExhibitsSource, dod_budget),
        (ForeignAssistanceSource, foreign),
        (DsipTopicsSource, dsip),
    ],
    ids=["reginfo", "darpa", "dod-budget", "foreign-assistance", "dsip"],
)
def test_program_selection_ranks_and_discloses_over_cap(
    monkeypatch, source_class, module,
):
    rows = []
    for index in range(105):
        base = {
            "record_id": f"{source_class.name}:{index:03d}",
            "title": f"Program row {index:03d}",
            "agency": "Department of Transportation",
        }
        if module is reginfo:
            base["timetable"] = [{
                "date": f"{(index % 12) + 1:02d}/01/{2030 - index // 12}",
            }]
        elif module is darpa:
            base["published_at"] = (
                f"2026-{(index % 12) + 1:02d}-01T00:00:00+00:00"
            )
        elif module is dod_budget:
            base["values"] = {"TOTAL": index * 1_000_000}
        elif module is foreign:
            base["current_amount"] = index * 1_000_000
        else:
            base.update({
                "status": "Open",
                "close_date": f"{2028 - index // 12}-{(index % 12) + 1:02d}-01",
                "topic_code": f"TOPIC-{index:03d}",
            })
        rows.append(base)

    rank = {
        reginfo: reginfo._agenda_rank,
        darpa: darpa._darpa_rank,
        dod_budget: dod_budget._budget_rank,
        foreign: foreign._assistance_rank,
        dsip: lambda row: (
            str(row.get("close_date") or "9999-12-31"),
            str(row.get("topic_code") or ""),
            str(row.get("record_id") or ""),
        ),
    }[module]
    expected_first = min(rows, key=rank)["record_id"]
    monkeypatch.setattr(
        module,
        "cached_program_pull",
        lambda **_kwargs: (list(reversed(rows)), {"status": "success"}),
    )

    out = source_class().enrich(SourceQuery(limit=100))

    assert len(out["records"]) == 100
    assert out["records"][0]["record_id"] == expected_first
    assert out["_provenance"]["records_matched"] == 105
    assert out["_provenance"]["candidate_total"] == 105
    assert out["_provenance"]["selected_count"] == 100
    assert out["_provenance"]["truncated"] is True
    assert out["_provenance"]["selection_order"]


def test_total_failure_is_returned_in_provenance_not_promoted(monkeypatch, tmp_path):
    _fixed_clock(monkeypatch)
    monkeypatch.setenv("LILA_DARPA_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(
        darpa,
        "get_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("feed down")),
    )

    out = DarpaOpportunitiesSource().enrich(SourceQuery())

    assert out["records"] == []
    assert out["_provenance"]["status"] == "failure"
    assert out["_provenance"]["promotion_eligible"] is False
    assert out["_provenance"]["retrieved_at"] == "2026-07-20T12:00:00+00:00"
    assert "feed down" in out["_provenance"]["error"]


def test_all_adapters_are_enrichment_only_and_cannot_emit_live_records():
    for source_class in (
        RegInfoUnifiedAgendaSource,
        DarpaOpportunitiesSource,
        DodBudgetExhibitsSource,
        ForeignAssistanceSource,
    ):
        source = source_class()
        assert source.kind == SourceKind.ENRICHMENT
        with pytest.raises(NotImplementedError, match="PROGRAM-tier"):
            source.search(SourceQuery())
