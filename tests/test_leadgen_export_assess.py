"""Read-only AssessRun export for Press Lead Gen.

Doctrine: dump the current pointer or validate a real AssessRun file.
Refuse notice-only, Market Map, source_records, and Testco-shaped
demo fixtures labeled as Arista. Never invent notices. Never write
data/state/assess_runs/. Never call materialize_current_assess_run.
Step 1 intake extract is untouched.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from agents.assess.ledger import persist_assess_run
from agents.leadgen.export_assess import (
    CHAIN_DOES_NOT_WRITE,
    DEMO_TESTCO,
    EXPORT_KIND,
    MARKET_MAP_REFUSED,
    NOTICE_ONLY,
    SOURCE_RECORDS_REFUSED,
    ExportAssessError,
    export_current_assess_run,
)
from agents.leadgen.export_assess import (
    main as export_main,
)
from agents.leadgen.press import run_press
from tests.test_assess_ledger import BINDING, _build, _sweep
from tests.test_leadgen_from_assess import _run

ROOT = Path(__file__).resolve().parents[1]
EXPORT_PY = ROOT / "agents" / "leadgen" / "export_assess.py"


def test_exports_current_pointer_for_press(tmp_path):
    run, diagnostics, index = _build(_sweep())
    path = persist_assess_run(
        run, BINDING, diagnostics, index, state_dir=tmp_path)
    payload = export_current_assess_run(
        "Testco", scope="all", state_dir=tmp_path)
    assert payload["export_kind"] == EXPORT_KIND
    assert payload["read_only"] is True
    assert payload["source"] == "current_pointer"
    assert payload["run"]["run_id"] == run.run_id
    assert payload["run"]["client_name"] == "Testco"
    assert payload["source_path"] == str(path)
    assert CHAIN_DOES_NOT_WRITE in payload["note"]
    live = payload["run"]["live"]["records"]
    assert live
    for record in live:
        urls = [
            item["source_url"]
            for item in record["authoritative_evidence"]
            if item.get("primary_source")
        ]
        assert urls
        assert all(url.startswith("https://") for url in urls)
    receipt = run_press(assess=payload, review_dir=tmp_path / "review")
    assert receipt.assess_run_id == run.run_id
    assert receipt.client_name == "Testco"


def test_missing_pointer_tells_cos_to_activate_not_invent(tmp_path):
    with pytest.raises(ExportAssessError) as refused:
        export_current_assess_run(
            "Arista Networks", scope="all", state_dir=tmp_path)
    message = str(refused.value)
    assert "no current AssessRun" in message
    assert "assess_refresh" in message
    assert "--activate" in message
    assert "Arista Networks" in message
    assert CHAIN_DOES_NOT_WRITE in message
    assert "source_records" in message
    assert "Market Map" in message
    assert list(tmp_path.rglob("*")) == []


def test_invalid_pointer_tells_cos_to_refresh(tmp_path):
    from agents.assess.ledger import current_assess_pointer_path
    pointer = current_assess_pointer_path(
        "Arista Networks", "all", state_dir=tmp_path)
    pointer.parent.mkdir(parents=True)
    pointer.write_text(json.dumps({
        "schema_version": 1,
        "run_id": "assess:v1:forged",
        "artifact": "missing.json",
        "artifact_sha256": "0" * 64,
    }), encoding="utf-8")
    with pytest.raises(ExportAssessError) as refused:
        export_current_assess_run(
            "Arista Networks", scope="all", state_dir=tmp_path)
    message = str(refused.value)
    assert "failed validation" in message
    assert "assess_refresh" in message
    assert "--activate" not in message
    assert "creation-free" in message


def test_refuses_notice_only_file(tmp_path):
    path = tmp_path / "notices.json"
    path.write_text(json.dumps({
        "notice_id": "N1",
        "title": "A notice is not an AssessRun",
        "solicitation_number": "70-TEST-26",
    }), encoding="utf-8")
    with pytest.raises(ExportAssessError) as refused:
        export_current_assess_run(
            "Arista Networks", from_file=path)
    assert str(refused.value) == NOTICE_ONLY


def test_refuses_sweep_without_assess_run(tmp_path):
    path = tmp_path / "searches_arista.json"
    path.write_text(json.dumps({
        "client": "Arista Networks",
        "results": {"sam.gov": [{"notice_id": "N1", "title": "Switch"}]},
    }), encoding="utf-8")
    with pytest.raises(ExportAssessError, match="notice-only"):
        export_current_assess_run(
            "Arista Networks", from_file=path)


def test_refuses_market_map_and_worksheet(tmp_path):
    market = tmp_path / "arista.market_map.json"
    market.write_text(json.dumps({
        "family": "lila_federal_market_map",
        "client_name": "Arista Networks",
        "slots": [],
    }), encoding="utf-8")
    with pytest.raises(ExportAssessError) as refused:
        export_current_assess_run("Arista Networks", from_file=market)
    assert str(refused.value) == MARKET_MAP_REFUSED
    worksheet = tmp_path / "arista.worksheet.json"
    worksheet.write_text(json.dumps({
        "worksheet_pack": True,
        "client_name": "Arista Networks",
        "worksheets": [{"title": "Live lane"}],
    }), encoding="utf-8")
    with pytest.raises(ExportAssessError, match="Market Map / worksheet"):
        export_current_assess_run("Arista Networks", from_file=worksheet)


def test_refuses_source_records_instead_of_inventing(tmp_path):
    path = tmp_path / "arista.source_records.json"
    path.write_text(json.dumps({
        "client_name": "Arista Networks",
        "source_records": [{
            "notice_id": "N1",
            "title": "Ethernet switching",
            "primary_source": "https://sam.gov/opp/N1/view",
        }],
    }), encoding="utf-8")
    with pytest.raises(ExportAssessError) as refused:
        export_current_assess_run("Arista Networks", from_file=path)
    assert str(refused.value) == SOURCE_RECORDS_REFUSED
    assert "invent" in str(refused.value)


def test_refuses_testco_demo_labeled_as_arista(tmp_path):
    path = tmp_path / "arista_demo.assess_run.json"
    path.write_text(
        json.dumps(_run().model_dump(mode="json")), encoding="utf-8")
    with pytest.raises(ExportAssessError) as refused:
        export_current_assess_run("Arista Networks", from_file=path)
    assert str(refused.value) == DEMO_TESTCO.format(
        requested="Arista Networks")
    assert "demo fixture" in str(refused.value)


def test_refuses_invented_live_records_without_primary_urls(tmp_path):
    payload = _run().model_dump(mode="json")
    for record in payload["live"]["records"]:
        for item in record["authoritative_evidence"]:
            item["primary_source"] = False
    path = tmp_path / "invented.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ExportAssessError) as refused:
        export_current_assess_run("Testco", from_file=path)
    assert "invented or notice-only live record refused" in str(refused.value)


def test_from_file_exports_matching_client(tmp_path):
    path = tmp_path / "testco.assess_run.json"
    run = _run()
    path.write_text(
        json.dumps(run.model_dump(mode="json")), encoding="utf-8")
    payload = export_current_assess_run("Testco", from_file=path)
    assert payload["source"] == "from_file"
    assert payload["run"]["run_id"] == "R1"
    assert payload["client_name"] == "Testco"
    receipt = run_press(assess=payload)
    assert receipt.assess_run_id == "R1"


def test_cli_writes_envelope_not_assess_runs(tmp_path, capsys):
    run, diagnostics, index = _build(_sweep())
    persist_assess_run(
        run, BINDING, diagnostics, index, state_dir=tmp_path / "runs")
    out = tmp_path / "desktop" / "testco.assess_run.json"
    assert export_main([
        "--client", "Testco",
        "--scope", "all",
        "--state-dir", str(tmp_path / "runs"),
        "--output", str(out),
    ]) == 0
    captured = capsys.readouterr()
    assert "[out]" in captured.err
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["run"]["run_id"] == run.run_id
    written = list((tmp_path / "runs").rglob("*"))
    assert out not in written
    assert "assess_runs" not in str(out)


def test_cli_refuses_write_into_assess_runs(tmp_path, capsys):
    run = _run()
    src = tmp_path / "ok.json"
    src.write_text(
        json.dumps(run.model_dump(mode="json")), encoding="utf-8")
    dest = tmp_path / "data" / "state" / "assess_runs" / "forged.json"
    dest.parent.mkdir(parents=True)
    assert export_main([
        "--client", "Testco",
        "--from-file", str(src),
        "--output", str(dest),
    ]) == 2
    assert "will not write into data/state/assess_runs" in capsys.readouterr().err
    assert not dest.exists()


def test_cli_refuses_notice_only(tmp_path, capsys):
    notices = tmp_path / "notices.json"
    notices.write_text(json.dumps({
        "results": {"sam.gov": [{"notice_id": "N1"}]},
    }), encoding="utf-8")
    assert export_main([
        "--client", "Arista Networks",
        "--from-file", str(notices),
    ]) == 2
    assert "notice-only" in capsys.readouterr().err


def test_exporter_does_not_import_materialize_or_intake():
    source = EXPORT_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = (
        "materialize_current_assess_run",
        "run_assessment",
        "assessment_chain",
        "lila_release",
        "product_bundle",
        "run_searches",
        "intake.extract",
        "agents.intake",
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
        else:
            continue
        joined = " ".join(modules).casefold()
        for token in forbidden:
            assert token not in joined, f"export_assess.py imports {token}"


def test_exporter_does_not_call_materialize(tmp_path, monkeypatch):
    calls = []

    def _boom(*_a, **_k):
        calls.append(True)
        raise AssertionError("materialize must not run")

    monkeypatch.setattr(
        "agents.assess.ledger.materialize_current_assess_run", _boom)
    with pytest.raises(ExportAssessError, match="no current AssessRun"):
        export_current_assess_run(
            "Arista Networks", scope="all", state_dir=tmp_path)
    assert calls == []
