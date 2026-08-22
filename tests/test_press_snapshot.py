"""Deterministic press-state snapshot contract."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.press_delta import canonical_json, compute_delta
from agents.press_snapshot import (
    PressSnapshotError,
    build_press_snapshot,
    canonical_snapshot_bytes,
    parse_signal_board_gate,
    write_press_snapshot,
)


PRESS_TIME = datetime(2026, 7, 17, 18, 30, tzinfo=timezone.utc)


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _write_gate(path: Path, *, verdict: str = "CLEAN") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    violation = (
        "\n## Violations\n- primary record is missing\n"
        if verdict == "DO-NOT-SEND"
        else ""
    )
    path.write_text(
        "# INTERNAL · Insignary · Signal Board press trail\n"
        "_render date 2026-07-16 · replay=True · never leaves the shop_\n\n"
        "## Gate verdict\n"
        f"- {verdict} (client-final)\n"
        f"{violation}\n"
        "## Relevance trail · taxonomy v1 (Insignary)\n",
        encoding="utf-8",
    )
    return path


def _fixture_tree(tmp_path: Path) -> dict[str, Path]:
    sweep = {
        "client": "Insignary",
        "generated_at": "2026-07-17T17:00:00+00:00",
        "search_scope": {"all": True},
        "results": {
            "sam.gov": [
                {
                    "source": "sam.gov",
                    "source_id": "NOTICE-CORE",
                    "title": "Software bill of materials validation",
                    "description": "SBOM component analysis for delivered software",
                    "naics_code": "541512",
                    "response_deadline": "2026-08-01",
                },
                {
                    "source": "sam.gov",
                    "source_id": "NOTICE-ADJACENT",
                    "title": "DevSecOps support",
                    "description": "DevSecOps delivery services",
                    "naics_code": "541512",
                },
            ],
            "triage": {},
            "usaspending.gov": [],
            "incumbent_buyer_map": {"buyers": []},
            "award_repulls": [
                {
                    "kind": "award_repull",
                    "generated_id": "CONT_AWD_TEST123_7003_-NONE-_-NONE-",
                    "award_id": "AWARD-CORE",
                    "description": "Binary SCA component analysis",
                    "amount": 100.0,
                    "potential_ceiling": 150.0,
                    "end_date": "2026-09-01",
                    "potential_end_date": "2027-09-01",
                    "retrieved_at": "2026-07-17T17:00:00+00:00",
                }
            ],
            "forecast_signals": {
                "matched": [{
                    "source": "dhs_apfs",
                    "source_id": "FORECAST-1",
                    "title": "Software assurance follow-on",
                    "anticipated_solicitation": "2026-10-01",
                    "anticipated_award": "Q2 FY2027",
                }]
            },
        },
    }
    content = {
        "client_name": "Insignary",
        "figures": [
            {
                "text": "$100",
                "raw": 100.0,
                "source_system": "usaspending",
                "source_record_id": "AWARD-CORE",
                "generated_internal_id": "CONT_AWD_TEST123_7003_-NONE-_-NONE-",
                "retrieved_at": "2026-07-17T17:00:00+00:00",
            },
            {
                "text": "$150",
                "raw": 150.0,
                "source_system": "usaspending",
                "source_record_id": "AWARD-CORE",
                "generated_internal_id": "CONT_AWD_TEST123_7003_-NONE-_-NONE-",
                "retrieved_at": "2026-07-17T17:00:00+00:00",
            },
            {
                "text": "$20B",
                "raw": 20_000_000_000.0,
                "source_system": "agency_doc",
                "source_url": "https://example.gov/program",
                "retrieved_at": "2026-07-17T17:00:00+00:00",
            },
        ],
    }
    calendar = {
        "client": "Insignary",
        "generated": "2026-07-17",
        "population": 1,
        "attack": [{
            "award_id": "RECOMPETE-1",
            "internal_id": "CONT_RECOMPETE_1",
            "pop_end": "2027-02-01",
            "score": 60,
        }],
        "defend": [],
    }
    return {
        "sweep": _write_json(
            tmp_path / "data" / "cleaned" / "searches_insignary.json", sweep
        ),
        "content": _write_json(
            tmp_path / "clients" / "insignary" / "signal_board_content.json",
            content,
        ),
        "recompete": _write_json(
            tmp_path / "data" / "state" / "recompete" / "insignary.json",
            calendar,
        ),
        "gate": _write_gate(
            tmp_path / "data" / "reports"
            / "insignary.federal_opportunity_signals.internal.md"
        ),
    }


def test_snapshot_is_canonical_complete_and_byte_identical(tmp_path):
    paths = _fixture_tree(tmp_path)
    kwargs = {
        "press_timestamp": PRESS_TIME,
        "root": tmp_path,
        "sweep_path": paths["sweep"],
        "content_path": paths["content"],
        "recompete_path": paths["recompete"],
        "gate_path": paths["gate"],
    }
    first = build_press_snapshot("Insignary", **kwargs)
    second = build_press_snapshot("Insignary", **kwargs)
    assert canonical_snapshot_bytes(first) == canonical_snapshot_bytes(second)

    assert first.schema_version == 1
    assert [(row.id, row.tier) for row in first.candidates] == [
        ("AWARD-CORE", "core"),
        ("NOTICE-CORE", "core"),
    ]
    screened = {row.id: row for row in first.candidate_screen}
    assert screened["NOTICE-ADJACENT"].tier == "adjacent"
    assert screened["NOTICE-ADJACENT"].relevant is False

    keyed = next(row for row in first.figures if row.source_record_id == "AWARD-CORE")
    assert keyed.comparable is True
    assert [(value.metric, value.value) for value in keyed.values] == [
        ("amount", 100.0),
        ("potential_ceiling", 150.0),
    ]
    unkeyed = next(row for row in first.figures if row.source_record_id is None)
    assert unkeyed.comparable is False
    assert unkeyed.key.startswith("unkeyed:agency_doc:")

    assert {row.field for row in first.windows} == {
        "response_deadline",
        "end_date",
        "potential_end_date",
        "anticipated_solicitation",
        "anticipated_award",
        "pop_end",
    }
    assert [row.source_record_id for row in first.recompete_events] == [
        "RECOMPETE-1"
    ]
    assert first.verification.model_dump() == {
        "status": "DEFERRED",
        "reason": "adversarial verify pass unavailable on this baseline",
        "verified": [],
        "disputed": [],
        "unverifiable": [],
    }

    path = write_press_snapshot(first, state_root=tmp_path / "deltas")
    before = path.read_bytes()
    assert json.loads(before)["client_name"] == "Insignary"
    assert write_press_snapshot(second, state_root=tmp_path / "deltas") == path
    assert path.read_bytes() == before


def test_gate_parser_accepts_latest_stored_local_render_date(tmp_path):
    gate = _write_gate(tmp_path / "sidecar.md", verdict="DO-NOT-SEND")
    parsed = parse_signal_board_gate(gate, press_timestamp=PRESS_TIME)
    assert parsed.render_date == "2026-07-16"
    assert parsed.status == "DO_NOT_SEND"
    assert parsed.violations == ["primary record is missing"]


def test_gate_state_normalizes_legacy_emdash_before_delta_render(tmp_path):
    gate = tmp_path / "sidecar.md"
    gate.write_text(
        "# INTERNAL · Insignary · Signal Board press trail\n"
        "_render date 2026-07-16 · replay=True · never leaves the shop_\n\n"
        "## Gate verdict\n"
        "- DO-NOT-SEND (violations below)\n\n"
        "## Violations\n"
        "- 'PURPOSE' belongs to client '_meta' — false-positive metadata\n",
        encoding="utf-8",
    )

    parsed = parse_signal_board_gate(gate, press_timestamp=PRESS_TIME)

    assert parsed.violations == [
        "'PURPOSE' belongs to client '_meta', false-positive metadata"
    ]
    assert "—" not in parsed.model_dump_json()


def test_clean_gate_cannot_carry_violations(tmp_path):
    path = tmp_path / "sidecar.md"
    path.write_text(
        "# INTERNAL\n"
        "_render date 2026-07-17 · replay=True_\n\n"
        "## Gate verdict\n- CLEAN (client-final)\n\n"
        "## Violations\n- contradiction\n",
        encoding="utf-8",
    )
    with pytest.raises(PressSnapshotError, match="CLEAN gate"):
        parse_signal_board_gate(path, press_timestamp=PRESS_TIME)


def test_naive_press_timestamp_fails_loudly(tmp_path):
    paths = _fixture_tree(tmp_path)
    with pytest.raises(PressSnapshotError, match="timezone-aware"):
        build_press_snapshot(
            "Insignary",
            press_timestamp=datetime(2026, 7, 17, 18, 30),
            root=tmp_path,
            sweep_path=paths["sweep"],
            content_path=paths["content"],
            recompete_path=paths["recompete"],
            gate_path=paths["gate"],
        )


def test_non_board_fact_pack_fallback_is_explicit_and_citation_bounded(
    tmp_path, monkeypatch
):
    from agents.reports.facts import Fact, FactPack, SourceSystem
    from agents.reports import facts

    paths = _fixture_tree(tmp_path)
    missing_content = tmp_path / "clients" / "insignary" / "missing.json"
    sentinel = tmp_path / "entity-telemetry-sentinel"
    monkeypatch.setenv("LILA_ENTITIES_DIR", str(sentinel))

    def fake_build_fact_pack(client_name, **kwargs):
        assert client_name == "Insignary"
        assert os.environ["LILA_ENTITIES_DIR"] != str(sentinel)
        assert kwargs["searches"]["client"] == "Insignary"
        return FactPack(
            client_name=client_name,
            as_of=PRESS_TIME.date(),
            facts=[
                Fact(
                    id="F1",
                    kind="market",
                    text="Award amount is $42.50.",
                    value={"amount": 42.5},
                    source="https://www.usaspending.gov/award/example",
                    source_system=SourceSystem.USASPENDING,
                    source_record_id="AWARD-FALLBACK",
                    retrieved_at=PRESS_TIME,
                ),
                Fact(
                    id="F2",
                    kind="context",
                    text="Program count is 3.",
                    value={"count": 3},
                    source="https://example.gov/program",
                    source_system=SourceSystem.AGENCY_DOC,
                    retrieved_at=PRESS_TIME,
                ),
            ],
        )

    monkeypatch.setattr(facts, "build_fact_pack", fake_build_fact_pack)
    common = {
        "press_timestamp": PRESS_TIME,
        "root": tmp_path,
        "sweep_path": paths["sweep"],
        "content_path": missing_content,
        "gate_state": {
            "status": "CLEAN",
            "render_date": "2026-07-17",
            "violations": [],
        },
    }
    source_fallback = build_press_snapshot("Insignary", **common)
    assert source_fallback.source_artifacts.figure_source == (
        "fact_pack_source_records"
    )
    by_record = {
        group.source_record_id: group for group in source_fallback.figures
    }
    assert set(by_record) == {"AWARD-FALLBACK", None}
    assert by_record["AWARD-FALLBACK"].comparable is True
    assert by_record[None].comparable is False
    assert os.environ["LILA_ENTITIES_DIR"] == str(sentinel)

    html = tmp_path / "data" / "reports" / "insignary.assessment.client.html"
    html.parent.mkdir(parents=True, exist_ok=True)
    html.write_text("<p>Award amount is $42.50. [F1]</p>", encoding="utf-8")
    cited_fallback = build_press_snapshot(
        "Insignary", client_html_path=html, **common
    )
    assert cited_fallback.source_artifacts.figure_source == "fact_pack_cited"
    assert [group.source_record_id for group in cited_fallback.figures] == [
        "AWARD-FALLBACK"
    ]


def test_live_insignary_snapshot_is_self_identical_and_read_only():
    root = Path(__file__).resolve().parents[1]
    sweep = root / "data" / "cleaned" / "searches_insignary.json"
    content = root / "clients" / "insignary" / "signal_board_content.json"
    gate = (
        root / "data" / "reports"
        / "insignary.federal_opportunity_signals.internal.md"
    )
    if not all(path.exists() for path in (sweep, content, gate)):
        pytest.skip("stored Insignary press state is not installed")
    telemetry = root / "data" / "entities" / "unresolved.log"
    before = telemetry.read_bytes() if telemetry.exists() else None
    first = build_press_snapshot(
        "Insignary",
        press_timestamp=PRESS_TIME,
        root=root,
        sweep_path=sweep,
        content_path=content,
        gate_path=gate,
    )
    second = build_press_snapshot(
        "Insignary",
        press_timestamp=PRESS_TIME,
        root=root,
        sweep_path=sweep,
        content_path=content,
        gate_path=gate,
    )
    assert canonical_snapshot_bytes(first) == canonical_snapshot_bytes(second)
    self_delta = compute_delta(first, second)
    assert self_delta["items"] == []
    assert canonical_json(self_delta) == canonical_json(compute_delta(first, second))
    assert sum(len(group.values) for group in first.figures) == 14
    assert len(first.candidates) == 9
    assert first.recompete_events == []
    after = telemetry.read_bytes() if telemetry.exists() else None
    assert after == before
