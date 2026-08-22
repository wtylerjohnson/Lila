"""Adversarial coverage for deterministic client-link integrity."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from types import SimpleNamespace

import httpx
import pytest

from agents.reports.board_content import (
    BoardFigure,
    SignalBoardContent,
    reconcile_federal_link_records,
)
from agents.reports.link_integrity import (
    DEFAULT_BACKOFF_S,
    DEFAULT_POLITENESS_S,
    DEFAULT_TIMEOUT_S,
    LinkClass,
    LinkCheckResult,
    USER_AGENT,
    agency_doc_link_inputs,
    agency_doc_claim_warnings,
    audit_client_links,
    claim_value_visible,
    dead_link_violations,
    manual_link_check_lines,
    render_link_integrity_internal_md,
    run_client_link_gate,
)
from agents.reports.facts import Fact, SourceSystem
from agents.reports.links import (
    build_sam_notice_link,
    build_usaspending_award_link,
)
from agents.reports.lint import lint_federal_link_construction
from agents.reports.signal_board import _link


AWARD_IDS = (
    "CONT_AWD_1333BJ21F00280023_1344_47QTCK18D0036_4732",
    "CONT_AWD_1333BJ24F00282021_1344_NNG15SD74B_8000",
    "CONT_AWD_1333BJ25F00280001_1344_47QTCK18D0036_4732",
    "CONT_AWD_1333BJ26F00282001_1344_NNG15SD26B_8000",
    "CONT_AWD_36C10B25F0304_3600_47QTCA23D00BT_4732",
    "CONT_AWD_70B04C25F00001054_7014_NNG15SC77B_8000",
    "CONT_AWD_70SBUR22F00000113_7003_HHSN316201200193W_7529",
    "CONT_AWD_70SBUR25F00000194_7003_HHSN316201500040W_7529",
    "CONT_AWD_70SBUR25F00000195_7003_NNG15SD08B_8000",
    "CONT_AWD_HR001124C0488_9700_-NONE-_-NONE-",
)
SAM_GUIDS = (
    "1e9c531df5a841c2935f370db3743213",
    "49ca8b1859c74fe28a4e9d3ae1c83d68",
    "692f3f7143d84b2da163ab1bcdd73b0b",
    "ae337f5d01e3499b980d347fa7567323",
    "b208e33b8dcd49088d751842a5f462aa",
)
NOW = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize("generated_id", AWARD_IDS)
def test_usaspending_builder_exact_insignary_forms(generated_id):
    link = build_usaspending_award_link(generated_id)
    assert link.url == f"https://www.usaspending.gov/award/{generated_id}"
    assert not link.url.endswith("/")


@pytest.mark.parametrize("guid", SAM_GUIDS)
def test_sam_builder_exact_insignary_forms(guid):
    link = build_sam_notice_link(guid)
    assert link.url == f"https://sam.gov/opp/{guid}/view"


@pytest.mark.parametrize(
    "bad",
    (
        "70SBUR22F00000113",
        " https://www.usaspending.gov/award/CONT_AWD_X",
        "CONT_AWD_X/extra",
        "CONT_AWD_X?query=1",
        "",
    ),
)
def test_award_builder_rejects_non_generated_identity(bad):
    with pytest.raises(ValueError):
        build_usaspending_award_link(bad)


@pytest.mark.parametrize(
    "bad",
    (
        "A" * 32,
        "abc",
        f"https://sam.gov/opp/{SAM_GUIDS[0]}/view",
        SAM_GUIDS[0] + "/view",
        " " + SAM_GUIDS[0],
    ),
)
def test_sam_builder_rejects_non_guid_input(bad):
    with pytest.raises(ValueError):
        build_sam_notice_link(bad)


def test_builder_provenance_lint_rejects_identical_hand_typed_url():
    link = build_sam_notice_link(SAM_GUIDS[0], reconciled=True)
    built = _link(link, "notice")
    raw = f'<a href="{link.url}">notice</a>'

    assert lint_federal_link_construction(built).ok
    result = lint_federal_link_construction(raw)
    assert not result.ok
    assert result.violations[0].rule == "federal_link_not_builder_derived"


def test_free_string_federal_url_anywhere_in_client_html_fails():
    url = build_usaspending_award_link(AWARD_IDS[0]).url
    result = lint_federal_link_construction(f"<!-- copied {url} -->")
    assert not result.ok
    assert result.violations[0].rule == "federal_link_free_string"


def test_free_string_sam_base_url_fails_but_non_sam_path_does_not():
    result = lint_federal_link_construction(
        "<!-- source portals (https://sam.gov) and sam.gov/opp/not-a-guid -->")
    assert not result.ok
    assert {violation.rule for violation in result.violations} == {
        "federal_link_free_string"}
    assert len(result.violations) == 2
    assert lint_federal_link_construction(
        "<!-- https://example.gov/archive/sam.gov/notice -->").ok


def test_expected_manifest_rejects_copy_pasted_valid_provenance_attrs():
    link = build_sam_notice_link(SAM_GUIDS[0], reconciled=True)
    rendered = _link(link, "expected")
    copied = rendered + _link(link, "forged copy")

    # Each anchor is internally canonical, so only the structured occurrence
    # manifest can prove the renderer was expected to emit it twice.
    assert lint_federal_link_construction(copied).ok
    result = lint_federal_link_construction(
        copied, expected_links=[link],
    )

    assert not result.ok
    assert {item.rule for item in result.violations} == {
        "federal_link_manifest_mismatch",
    }


def test_unquoted_canonical_href_is_parsed_and_manifest_checked():
    link = build_usaspending_award_link(AWARD_IDS[0], reconciled=True)
    html = (
        f"<a href={link.url} data-link-builder={link.builder} "
        f"data-link-record={link.record_id} data-link-reconciled=1>award</a>"
    )

    assert lint_federal_link_construction(
        html, expected_links=[link],
    ).ok


def test_duplicate_href_is_blocked_without_fetch(tmp_path):
    link = build_sam_notice_link(SAM_GUIDS[0], reconciled=True)
    html = (
        f'<a href="{link.url}" href="https://example.gov/decoy" '
        f'data-link-builder="{link.builder}" '
        f'data-link-record="{link.record_id}" '
        'data-link-reconciled="1">notice</a>'
    )

    lint = lint_federal_link_construction(html, expected_links=[link])
    assert not lint.ok
    assert "federal_link_not_builder_derived" in {
        item.rule for item in lint.violations
    }

    def bomb(_url):
        raise AssertionError("duplicate href reached HTTP")

    result = audit_client_links(
        html, cache_dir=tmp_path, fetch=bomb,
    )[0]
    assert result.classification == LinkClass.BUILDER_BLOCKED
    assert len(dead_link_violations([result])) == 1


def _content(*, award_id=AWARD_IDS[0], guid=SAM_GUIDS[1]):
    return SignalBoardContent(
        client_name="Testco",
        news=[{"head": "award", "award_generated_id": award_id},
              {"head": "notice", "sam_notice_guid": guid}],
        figures=[BoardFigure(
            text="$20B", raw=20_000_000_000,
            source_system="usaspending", source_record_id="PIID-1",
            generated_internal_id=award_id, retrieved_at=NOW,
        )],
    )


def _sweep(*, award_id=AWARD_IDS[0], guid=SAM_GUIDS[1], retrieved=NOW,
           disputed=False):
    stamp = retrieved.isoformat() if retrieved is not None else None
    award = {"generated_id": award_id, "award_id": "PIID-1",
             "retrieved_at": stamp, "disputed": disputed,
             "amount": 20_000_000_000}
    notice = {"source_id": guid, "retrieved_at": stamp,
              "disputed": disputed}
    return {"generated_at": stamp,
            "results": {"award_repulls": [award], "sam.gov": [notice]}}


def test_record_reconciliation_present_fresh_undisputed_passes():
    result = reconcile_federal_link_records(_content(), _sweep(), now=NOW)
    assert result.clean
    assert len(result.records) == 2
    assert all(result.status_map.values())


def test_candidate_teaming_award_link_is_in_the_record_reconciliation_gate():
    content = SignalBoardContent(
        client_name="Testco",
        teaming=[{
            "client": "TESTCO", "partner": "FCN",
            "target": "SolarWinds account",
            "angle": "SOLARWINDS AWARD AT IRS · THROUGH 12 MAY 2027",
            "proof": "AWARD PIID-1 · VALIDATION-REQUIRED THESIS",
            "award_generated_id": AWARD_IDS[0],
            "citation_label": "award PIID-1",
        }],
        figures=[BoardFigure(
            text="$20B", raw=20_000_000_000,
            source_system="usaspending", source_record_id="PIID-1",
            generated_internal_id=AWARD_IDS[0], retrieved_at=NOW,
        )],
    )

    result = reconcile_federal_link_records(content, _sweep(), now=NOW)

    assert result.clean
    assert result.status_map == {
        ("usaspending_award", AWARD_IDS[0]): True,
    }
    assert "Candidate teaming opportunities" in \
        result.records[0].ref.locations[0]


def test_teaming_machine_evidence_does_not_inflate_rendered_link_manifest(
        tmp_path):
    from agents.reports.lint import lint_federal_link_construction
    from agents.reports.signal_board import (
        _materialize_federal_links,
        canonical_federal_link_manifest,
        render_signal_board,
    )

    generated_id = AWARD_IDS[0]
    model = _materialize_federal_links({
        "client_name": "Testco",
        "report_date": "17 JUL 2026",
        "teaming": [{
            "client": "TESTCO",
            "partner": "FCN",
            "target": "SolarWinds account",
            "angle": "SOLARWINDS AWARD AT IRS · THROUGH 12 MAY 2027",
            "proof": "AWARD PIID-1 · VALIDATION-REQUIRED THESIS",
            "award_generated_id": generated_id,
            "citation_label": "award PIID-1",
            "machine_evidence": {
                "source_kind": "corridor-entry-thesis",
                "source_identity": generated_id,
                "route_basis": {
                    "kind": "entry-thesis",
                    "award_generated_id": generated_id,
                },
            },
        }],
    }, {("usaspending_award", generated_id): True})

    manifest = canonical_federal_link_manifest(model)
    rendered = render_signal_board(model)

    assert model["teaming"][0]["machine_evidence"]["route_basis"][
        "award_generated_id"] == generated_id
    assert len(manifest) == 1
    assert rendered.count(f'data-link-record="{generated_id}"') == 1
    assert lint_federal_link_construction(
        rendered, expected_links=manifest).ok
    results = audit_client_links(
        rendered,
        cache_dir=tmp_path,
        fetch=lambda _url: pytest.fail("canonical SPA link reached HTTP"),
        expected_federal_links=manifest,
    )
    assert len(results) == 1
    assert results[0].classification == LinkClass.BUILDER_RECONCILED


def test_verified_figure_identity_seeds_dynamic_award_link_status():
    content = SignalBoardContent(
        client_name="Testco",
        figures=[BoardFigure(
            text="$20B", raw=20_000_000_000,
            source_system="usaspending", source_record_id="PIID-1",
            generated_internal_id=AWARD_IDS[0], retrieved_at=NOW,
        )],
    )
    result = reconcile_federal_link_records(content, _sweep(), now=NOW)

    assert result.clean
    assert result.status_map == {
        ("usaspending_award", AWARD_IDS[0]): True,
    }
    assert "Figure registry" in result.records[0].ref.locations[0]


def test_record_reconciliation_absent_mismatch_stale_and_disputed_block():
    absent = reconcile_federal_link_records(
        _content(), {"generated_at": NOW.isoformat(), "results": {}}, now=NOW)
    assert {v.rule for v in absent.violations} == {
        "FEDERAL_LINK_RECORD_ABSENT"}

    mismatch_sweep = _sweep()
    mismatch_sweep["results"]["award_repulls"][0]["award_id"] = "OTHER"
    mismatch = reconcile_federal_link_records(
        _content(), mismatch_sweep, now=NOW)
    assert "FEDERAL_LINK_RECORD_MISMATCH" in {
        v.rule for v in mismatch.violations}

    stale = reconcile_federal_link_records(
        _content(), _sweep(retrieved=NOW - timedelta(days=15)), now=NOW)
    assert "FRESHNESS_STALE" in {v.rule for v in stale.violations}

    disputed = reconcile_federal_link_records(
        _content(), _sweep(disputed=True), now=NOW)
    assert "FIGURE_DISPUTED" in {v.rule for v in disputed.violations}


def _html(url: str, section: str = "Evidence") -> str:
    return f'<section><h2>{section}</h2><a href="{url}">source</a></section>'


def _response(
    status: int,
    *,
    text: str = "page",
    url: str = "https://example.gov/final",
    headers: dict[str, str] | None = None,
):
    return SimpleNamespace(
        status_code=status,
        text=text,
        url=url,
        headers=headers or {},
    )


@pytest.mark.parametrize(
    ("responses", "expected", "calls"),
    (
        ([_response(200)], LinkClass.OK, 1),
        ([_response(204)], LinkClass.OK, 1),
        ([_response(404)], LinkClass.DEAD, 1),
        ([_response(410)], LinkClass.DEAD, 1),
        ([_response(403)], LinkClass.UNVERIFIABLE, 1),
        ([_response(429)], LinkClass.UNVERIFIABLE, 1),
        ([_response(500), _response(503)], LinkClass.DEAD, 2),
        ([_response(500), _response(200)], LinkClass.OK, 2),
        ([_response(401)], LinkClass.UNVERIFIABLE, 1),
    ),
)
def test_external_http_classification_paths(
    tmp_path, responses, expected, calls,
):
    seen = []

    def fetch(url):
        seen.append(url)
        return responses.pop(0)

    result = audit_client_links(
        _html("https://example.gov/doc"), cache_dir=tmp_path,
        as_of=date(2026, 7, 17), fetch=fetch, sleeper=lambda _s: None,
    )[0]
    assert result.classification == expected
    assert len(seen) == calls


def test_production_checker_uses_get_browser_headers_timeout_and_manual_redirects(
    tmp_path, monkeypatch,
):
    captured = {"requests": []}

    class Response:
        status_code = 200
        url = "https://example.gov/doc"
        headers = {}
        encoding = "utf-8"

        @staticmethod
        def iter_bytes():
            yield b"alive"

    class Stream:
        def __enter__(self):
            return Response()

        def __exit__(self, *_args):
            return False

    class Client:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def stream(self, method, url):
            captured["requests"].append((method, url))
            return Stream()

    monkeypatch.setattr("agents.reports.link_integrity.httpx.Client", Client)
    result = audit_client_links(
        _html("https://example.gov/doc"), cache_dir=tmp_path,
        resolver=lambda _url: None,
    )[0]

    assert result.classification == LinkClass.OK
    assert captured["requests"] == [("GET", "https://example.gov/doc")]
    assert captured["kwargs"] == {
        "headers": {"User-Agent": USER_AGENT},
        "timeout": DEFAULT_TIMEOUT_S,
        "follow_redirects": False,
    }


def test_retry_backoff_and_between_url_politeness_are_deterministic(tmp_path):
    sleeps = []
    responses = {
        "https://one.example/a": [_response(500), _response(200)],
        "https://two.example/b": [_response(200)],
    }

    def fetch(url):
        return responses[url].pop(0)

    html = (_html("https://one.example/a", "One")
            + _html("https://two.example/b", "Two"))
    results = audit_client_links(
        html, cache_dir=tmp_path, fetch=fetch, sleeper=sleeps.append,
    )

    assert all(result.classification == LinkClass.OK for result in results)
    assert sleeps == [DEFAULT_BACKOFF_S, DEFAULT_POLITENESS_S]


@pytest.mark.parametrize(
    "error",
    (
        httpx.ReadTimeout("slow"),
        httpx.ConnectError("offline"),
        ConnectionError("offline"),
    ),
)
def test_timeout_and_connection_errors_are_unverifiable(tmp_path, error):
    calls = []

    def fetch(url):
        calls.append(url)
        raise error

    result = audit_client_links(
        _html("https://example.gov/doc"), cache_dir=tmp_path,
        fetch=fetch, sleeper=lambda _s: None,
    )[0]
    assert result.classification == LinkClass.UNVERIFIABLE
    assert len(calls) == 2


def test_spa_domains_never_reach_http_checker(tmp_path):
    links = (
        build_usaspending_award_link(AWARD_IDS[0], reconciled=True),
        build_sam_notice_link(SAM_GUIDS[0], reconciled=True),
    )
    html = "".join((_link(links[0], "award"), _link(links[1], "notice")))

    def bomb(_url):
        raise AssertionError("SPA-domain HTTP check ran")

    results = audit_client_links(
        html, cache_dir=tmp_path, fetch=bomb,
        expected_federal_links=links,
    )
    assert len(results) == 2
    assert {result.classification for result in results} == {
        LinkClass.BUILDER_RECONCILED}


def test_forged_reconciliation_attributes_do_not_satisfy_audit(tmp_path):
    forged = build_sam_notice_link(SAM_GUIDS[0], reconciled=True)
    result = audit_client_links(
        _link(forged, "notice"), cache_dir=tmp_path,
        fetch=lambda _url: pytest.fail("SPA HTTP check ran"),
    )[0]

    assert result.classification == LinkClass.BUILDER_BLOCKED
    assert "manifest=no" in result.proof


def test_unreconciled_spa_link_is_reported_blocked_without_http(tmp_path):
    html = _link(build_sam_notice_link(SAM_GUIDS[0]), "notice")

    def bomb(_url):
        raise AssertionError("SPA-domain HTTP check ran")

    result = audit_client_links(html, cache_dir=tmp_path, fetch=bomb)[0]
    assert result.classification == LinkClass.BUILDER_BLOCKED
    assert "reconciled=no" in result.proof


@pytest.mark.parametrize(
    ("url", "expected"),
    (
        (f"//sam.gov/opp/{SAM_GUIDS[0]}/view",
         LinkClass.BUILDER_BLOCKED),
        (f"https://sam.gov./opp/{SAM_GUIDS[0]}/view",
         LinkClass.BUILDER_BLOCKED),
        (f"https://sam.gov@evil.example/opp/{SAM_GUIDS[0]}/view",
         LinkClass.INVALID),
    ),
)
def test_noncanonical_federal_host_spellings_block_without_fetch(
    tmp_path, url, expected,
):
    calls = []

    def fetch(target):
        calls.append(target)
        raise AssertionError("noncanonical federal link reached HTTP")

    result = audit_client_links(
        _html(url), cache_dir=tmp_path, fetch=fetch,
    )[0]

    assert result.classification == expected
    assert calls == []
    assert len(dead_link_violations([result])) == 1


@pytest.mark.parametrize(
    "url",
    (
        f"https://sam\u3002gov/opp/{SAM_GUIDS[0]}/view",
        f"https://www\uff0eusaspending\uff61gov/award/{AWARD_IDS[0]}",
    ),
)
def test_idna_dot_federal_hosts_are_gated_without_fetch(tmp_path, url):
    calls = []

    def fetch(target):
        calls.append(target)
        raise AssertionError("IDNA-dot federal link reached HTTP")

    result = audit_client_links(
        _html(url), cache_dir=tmp_path, fetch=fetch,
    )[0]

    assert result.classification == LinkClass.BUILDER_BLOCKED
    assert calls == []


@pytest.mark.parametrize(
    "url",
    (
        "https://sam.gov%2eevil.example/notice",
        "https://sam.gov\\evil.example/notice",
        f"https://evil.example\\@sam.gov/opp/{SAM_GUIDS[0]}/view",
        "https://sam.gov%2f@evil.example/notice",
    ),
)
def test_ambiguous_federal_authorities_are_invalid_without_fetch(
    tmp_path, url,
):
    calls = []

    def fetch(target):
        calls.append(target)
        raise AssertionError("ambiguous federal authority reached HTTP")

    result = audit_client_links(
        _html(url), cache_dir=tmp_path, fetch=fetch,
    )[0]

    assert result.classification == LinkClass.INVALID
    assert calls == []


def test_external_redirect_to_idna_dot_federal_host_is_not_fetched(tmp_path):
    calls = []
    federal_url = f"https://sam\u3002gov/opp/{SAM_GUIDS[0]}/view"

    def fetch(url):
        calls.append(url)
        return _response(302, url=url, headers={"location": federal_url})

    result = audit_client_links(
        _html("https://example.gov/start"), cache_dir=tmp_path, fetch=fetch,
    )[0]

    assert result.classification == LinkClass.INVALID
    assert "builder reconciliation" in result.proof
    assert calls == ["https://example.gov/start"]


def test_private_ip_literal_blocks_without_fetch(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        raise AssertionError("private target reached HTTP")

    result = audit_client_links(
        _html("http://127.0.0.1/admin"), cache_dir=tmp_path, fetch=fetch,
    )[0]

    assert result.classification == LinkClass.INVALID
    assert "non-public IP" in result.proof
    assert calls == []
    assert len(dead_link_violations([result])) == 1


def test_redirect_target_is_validated_before_second_fetch(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return _response(
            302,
            url=url,
            headers={"location": "http://127.0.0.1/private"},
        )

    result = audit_client_links(
        _html("https://example.gov/start"),
        cache_dir=tmp_path,
        fetch=fetch,
    )[0]

    assert result.classification == LinkClass.INVALID
    assert "non-public IP" in result.proof
    assert calls == ["https://example.gov/start"]


def test_relative_redirect_uses_case_insensitive_location_header(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        if url.endswith("/start"):
            return _response(302, url=url, headers={"Location": "/final"})
        return _response(200, url=url)

    result = audit_client_links(
        _html("https://example.gov/start"), cache_dir=tmp_path, fetch=fetch,
    )[0]

    assert result.classification == LinkClass.OK
    assert calls == ["https://example.gov/start", "https://example.gov/final"]


def test_daily_cache_prevents_rehammer_and_rolls_next_day(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return _response(200, text="$20 billion")

    html = _html("https://example.gov/doc")
    first = audit_client_links(
        html, cache_dir=tmp_path, as_of=date(2026, 7, 17), fetch=fetch)
    second = audit_client_links(
        html, cache_dir=tmp_path, as_of=date(2026, 7, 17), fetch=fetch)
    third = audit_client_links(
        html, cache_dir=tmp_path, as_of=date(2026, 7, 18), fetch=fetch)

    assert len(calls) == 2
    assert not first[0].from_cache and second[0].from_cache
    assert not third[0].from_cache
    assert [row.classification for row in first] == [
        row.classification for row in second]


@pytest.mark.parametrize(
    ("status", "classification"),
    (
        (200, LinkClass.OK),
        (404, LinkClass.DEAD),
        (401, LinkClass.UNVERIFIABLE),
    ),
)
def test_daily_cache_replays_identical_result_and_manual_line(
    tmp_path, status, classification,
):
    calls = []

    def fetch(url):
        calls.append(url)
        return _response(status, text="stable response body", url=url)

    kwargs = {
        "cache_dir": tmp_path,
        "as_of": date(2026, 7, 17),
        "fetch": fetch,
    }
    first = audit_client_links(_html("https://example.gov/doc"), **kwargs)
    second = audit_client_links(_html("https://example.gov/doc"), **kwargs)

    assert first[0].classification == classification
    assert {**first[0].__dict__, "from_cache": False} == {
        **second[0].__dict__, "from_cache": False,
    }
    assert manual_link_check_lines(first) == manual_link_check_lines(second)
    assert calls == ["https://example.gov/doc"]


def test_daily_cache_replays_transport_failure_manual_line(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        raise httpx.ConnectError("network unavailable")

    kwargs = {
        "cache_dir": tmp_path,
        "as_of": date(2026, 7, 17),
        "fetch": fetch,
        "sleeper": lambda _seconds: None,
    }
    first = audit_client_links(_html("https://example.gov/doc"), **kwargs)
    second = audit_client_links(_html("https://example.gov/doc"), **kwargs)

    assert {**first[0].__dict__, "from_cache": False} == {
        **second[0].__dict__, "from_cache": False,
    }
    assert manual_link_check_lines(first) == manual_link_check_lines(second)
    assert len(calls) == 2


@pytest.mark.parametrize("tamper", ("contradictory_class", "unsafe_final"))
def test_tampered_daily_cache_is_not_trusted(tmp_path, tamper):
    calls = []

    def fetch(url):
        calls.append(url)
        return _response(200, text="current source", url=url)

    kwargs = {
        "cache_dir": tmp_path,
        "as_of": date(2026, 7, 17),
        "fetch": fetch,
    }
    first = audit_client_links(_html("https://example.gov/doc"), **kwargs)
    cache_path = next(tmp_path.rglob("*.json"))
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    if tamper == "contradictory_class":
        payload["classification"] = LinkClass.DEAD.value
    else:
        payload["final_url"] = "http://127.0.0.1/private"
    cache_path.write_text(json.dumps(payload), encoding="utf-8")

    second = audit_client_links(_html("https://example.gov/doc"), **kwargs)

    assert first[0].classification == LinkClass.OK
    assert second[0].classification == LinkClass.OK
    assert not second[0].from_cache
    assert calls == ["https://example.gov/doc"] * 2


def test_impossible_cached_response_history_is_not_trusted(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return _response(200, url=url)

    kwargs = {"cache_dir": tmp_path, "as_of": date(2026, 7, 17),
              "fetch": fetch}
    audit_client_links(_html("https://example.gov/doc"), **kwargs)
    cache_path = next(tmp_path.rglob("*.json"))
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    payload.update(status_history=[404, 200], status_code=200,
                   classification=LinkClass.OK.value)
    cache_path.write_text(json.dumps(payload), encoding="utf-8")

    result = audit_client_links(_html("https://example.gov/doc"), **kwargs)

    assert not result[0].from_cache
    assert calls == ["https://example.gov/doc"] * 2


def test_repeated_audit_is_semantically_idempotent(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return _response(200, text="stable body", url=url)

    kwargs = {
        "cache_dir": tmp_path,
        "as_of": date(2026, 7, 17),
        "fetch": fetch,
    }
    first = audit_client_links(_html("https://example.gov/doc"), **kwargs)
    second = audit_client_links(_html("https://example.gov/doc"), **kwargs)

    first_payload = {**first[0].__dict__, "from_cache": False}
    second_payload = {**second[0].__dict__, "from_cache": False}
    assert second_payload == first_payload
    assert calls == ["https://example.gov/doc"]
    assert len(list(tmp_path.rglob("*.json"))) == 1


def test_offline_is_unverifiable_without_fetch_or_cache_lookup(tmp_path):
    def bomb(_url):
        raise AssertionError("offline audit performed HTTP")

    result = audit_client_links(
        _html("https://example.gov/doc"), enabled=False,
        cache_dir=tmp_path, fetch=bomb,
    )[0]
    assert result.classification == LinkClass.UNVERIFIABLE
    assert "disabled" in result.proof


def test_dead_and_manual_results_name_url_and_section(tmp_path):
    dead = audit_client_links(
        _html("https://example.gov/dead", "Competitor lanes"),
        cache_dir=tmp_path / "dead", fetch=lambda _url: _response(404),
    )
    violations = dead_link_violations(dead)
    assert len(violations) == 1
    assert "Competitor lanes" in violations[0].detail
    assert "https://example.gov/dead" in violations[0].detail

    duplicate = (
        _html("https://example.gov/manual", "One")
        + _html("https://example.gov/manual", "Two"))
    manual = audit_client_links(
        duplicate, enabled=False, cache_dir=tmp_path / "manual")
    lines = manual_link_check_lines(manual)
    assert len(lines) == 1
    assert "One" in lines[0] and "Two" in lines[0]


def test_section_attribution_resets_after_section_close(tmp_path):
    html = (
        '<section><h2>Evidence</h2><a href="https://one.example/a">one</a>'
        '</section><footer><a href="https://two.example/b">two</a></footer>'
    )
    results = audit_client_links(html, enabled=False, cache_dir=tmp_path)
    by_url = {result.url: result.sections for result in results}

    assert by_url["https://one.example/a"] == ("Evidence",)
    assert by_url["https://two.example/b"] == ("document",)


def test_nonblank_aria_label_scopes_section_attribution(tmp_path):
    html = (
        '<section><h2>Hero</h2>'
        '<div aria-label="  Relevant research signals  ">'
        '<div><a href="https://news.example/item">news</a></div>'
        '<a href="https://news.example/second">second news</a></div>'
        '<div aria-label="   "><a href="https://blank.example/item">blank</a>'
        '</div><a href="https://hero.example/item">hero</a></section>'
    )
    results = audit_client_links(html, enabled=False, cache_dir=tmp_path)
    by_url = {result.url: result.sections for result in results}

    assert by_url["https://news.example/item"] == (
        "Relevant research signals",
    )
    assert by_url["https://news.example/second"] == (
        "Relevant research signals",
    )
    assert by_url["https://blank.example/item"] == ("Hero",)
    assert by_url["https://hero.example/item"] == ("Hero",)


def test_shared_gate_renders_manual_check_without_blocking(tmp_path):
    outcome = run_client_link_gate(
        _html("https://example.gov/manual", "Sources"), enabled=False,
        cache_dir=tmp_path,
    )
    sidecar = render_link_integrity_internal_md("Testco", outcome)

    assert outcome.violations == ()
    assert len(outcome.manual_checks) == 1
    assert "## MANUAL LINK CHECK" in sidecar
    assert "https://example.gov/manual" in sidecar


def _agency_fact(value, *, url="https://agency.gov/official"):
    return Fact(
        id="F9",
        kind="context",
        text="Official agency prose describing the cited program.",
        value=value,
        source=url,
        source_system=SourceSystem.AGENCY_DOC,
        source_record_id="agency-record",
    )


def test_agency_doc_adapter_extracts_structured_amount_count_and_date():
    fact = _agency_fact({
        "amount": 20_000_000_000,
        "award_count": 7,
        "publication_date": "2026-07-17",
        "narrative": "not a claim value",
    })

    links, claims = agency_doc_link_inputs([fact])

    assert links == [(fact.source, "Figure provenance")]
    assert [(claim.claim_field, claim.raw, claim.text) for claim in claims] == [
        ("amount", 20_000_000_000, "20000000000"),
        ("award_count", 7, "7"),
        ("publication_date", "2026-07-17", "2026-07-17"),
    ]
    assert {claim.fact_id for claim in claims} == {"F9"}


def test_shared_gate_adds_agency_doc_fact_source_as_extra_link(tmp_path):
    fact = _agency_fact({"amount": 20_000_000_000})

    outcome = run_client_link_gate(
        "", enabled=False, figures=[fact], cache_dir=tmp_path)

    assert [result.url for result in outcome.results] == [fact.source]
    assert outcome.results[0].classification == LinkClass.UNVERIFIABLE
    assert outcome.results[0].sections == ("Figure provenance",)
    assert len(outcome.manual_checks) == 1
    assert outcome.claim_warnings == ()


def test_structured_fact_claims_never_match_fact_prose_instead_of_value():
    fact = _agency_fact({"amount": 20_000_000_000})
    prose_only = [LinkCheckResult(
        url=fact.source,
        classification=LinkClass.OK,
        sections=("Figure provenance",),
        proof="GET terminal 200",
        text=fact.text,
    )]

    warnings = agency_doc_claim_warnings([fact], prose_only)

    assert len(warnings) == 1
    assert "agency-record.amount" in warnings[0]
    assert "20000000000" in warnings[0]


def test_structured_amount_count_and_date_match_visible_scalar_values():
    fact = _agency_fact({
        "amount": 20_000_000_000,
        "award_count": 7,
        "publication_date": "2026-07-17",
    })
    fetched = [LinkCheckResult(
        url=fact.source,
        classification=LinkClass.OK,
        sections=("Figure provenance",),
        proof="GET terminal 200",
        text=("<p>Ceiling: $20 billion. Seven-row summary: 7 awards. "
              "Published 2026-07-17.</p>"),
    )]

    assert agency_doc_claim_warnings([fact], fetched) == []


@pytest.mark.parametrize(
    "body",
    ("Maximum value: $20 billion.", "Ceiling $20B", "20,000,000,000"),
)
def test_agency_doc_claim_normalization_variants(body):
    assert claim_value_visible("$20B", 20_000_000_000, body)
    assert not claim_value_visible("$20B", 20_000_000_000,
                                   "Maximum value: $120B")


def test_hidden_or_script_amount_cannot_satisfy_claim_visibility():
    body = """
    <script>window.ceiling = 289500000;</script>
    <div hidden>$289.5M</div>
    <span aria-hidden="true">289,500,000</span>
    <p>The contract ceiling is described elsewhere.</p>
    """
    assert not claim_value_visible("$289.5M", 289_500_000, body)


def test_css_whitespace_hidden_amount_and_loose_scale_do_not_match():
    hidden = '<div style="display:\t none">$20B</div><p>No ceiling.</p>'
    assert not claim_value_visible("$20B", 20_000_000_000, hidden)
    assert not claim_value_visible("$20B", 20_000_000_000,
                                   "The document has 20 m of cable.")


def test_display_and_raw_value_must_agree_before_page_can_match():
    assert not claim_value_visible("$20B", 21_000_000_000,
                                   "The ceiling is $20B.")


def test_rounded_display_form_is_exact_numeric_match():
    body = "<p>The maximum contract value is $289.5M.</p>"
    assert claim_value_visible("$289.5M", 289_500_000, body)


def test_claim_absence_warns_only_after_ok_fetch(tmp_path):
    url = "https://www.nasa.gov/news-release/example/"
    figure = BoardFigure(
        text="$20B", raw=20_000_000_000, source_system="agency_doc",
        source_record_id="sewpvi-ceiling", source_url=url, retrieved_at=NOW)
    ok = audit_client_links(
        "", extra_links=[(url, "Figure provenance")],
        cache_dir=tmp_path / "claim-cache",
        as_of=date(2026, 7, 17),
        fetch=lambda _url: _response(200, text="No ceiling on this page"),
    )
    warnings = agency_doc_claim_warnings([figure], ok)
    assert len(warnings) == 1
    assert "sewpvi-ceiling" in warnings[0] and url in warnings[0]

    unverifiable = [ok[0].__class__(
        **{**ok[0].__dict__, "classification": LinkClass.UNVERIFIABLE})]
    assert agency_doc_claim_warnings([figure], unverifiable) == []
