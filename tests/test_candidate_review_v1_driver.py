"""Driver (Chunk 6, Pass E) end-to-end tests: one-click zero-LLM pipeline.

Creates a real watch generation with the wiring harness, then drives
compose -> render -> certify -> persist -> marker with NO fetchers and NO
seed-author adapter, proving the default path produces a valid certified
document and spends nothing.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

import run_candidate_review as driver
import run_candidate_review_watch as watch_cli
from agents.assessment_chain import validate_candidate_review_document_marker
from agents.candidate_review_v1.contracts import SECTION_ORDER, CandidateReviewDocument
from agents.candidate_review_v1.document_release import validate_certificate_schema

_WIRING_PATH = Path(__file__).parent / "test_candidate_review_v1_wiring.py"
_AS_OF = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)


def _wiring():
    spec = importlib.util.spec_from_file_location("_crv1_wiring", _WIRING_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_generation(tmp_path, monkeypatch):
    registry = _wiring()._approved_world(tmp_path, monkeypatch)
    watch_cli.run_watch_generation("riverbed", root=tmp_path, registry_path=registry)
    return tmp_path / "data" / "state" / "candidate_review_v1"


def test_default_path_composes_certifies_and_persists_a_document(tmp_path, monkeypatch):
    state_root = _make_generation(tmp_path, monkeypatch)
    qa_path = driver.run_candidate_review(
        "riverbed", root=tmp_path, state_root=state_root, certified_at=_AS_OF)
    assert qa_path.is_file()
    import json
    payload = json.loads(qa_path.read_text(encoding="utf-8"))
    validate_certificate_schema(payload)
    assert payload["client_id"] == "riverbed"
    assert payload["state"] == "DRAFT" and payload["release_eligible"] is False
    assert payload["section_ids"] == [s.section_id for s in SECTION_ORDER]
    # the editable HTML and the document sidecar were written
    client_dir = state_root / "riverbed"
    assert (client_dir / "riverbed.candidate_review.DRAFT.html").is_file()
    doc_json = client_dir / "riverbed.candidate_review.document.json"
    assert doc_json.is_file()
    CandidateReviewDocument.model_validate_json(doc_json.read_text(encoding="utf-8"))


def test_cli_emits_exactly_one_valid_marker(tmp_path, monkeypatch, capsys):
    state_root = _make_generation(tmp_path, monkeypatch)
    rc = driver.main(["--client", "riverbed", "--state-root", str(state_root)])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.count("[out:candidate-review-document]") == 1
    resolved = validate_candidate_review_document_marker(tmp_path, "riverbed", out)
    assert resolved.name == "riverbed.candidate_review.qa.json"


def test_missing_generation_fails_named_with_rc_2(tmp_path, monkeypatch, capsys):
    # no generation created
    state_root = tmp_path / "data" / "state" / "candidate_review_v1"
    rc = driver.main(["--client", "riverbed", "--state-root", str(state_root)])
    assert rc == 2
    err = capsys.readouterr().err.strip()
    import json
    obj = json.loads(err)
    assert obj["stage"] == "candidate-review-document" and obj["reason"]
    assert "[out:candidate-review-document]" not in capsys.readouterr().out


def test_default_path_spends_nothing(tmp_path, monkeypatch):
    state_root = _make_generation(tmp_path, monkeypatch)

    import agents.decisions.maxplan_cli as maxplan

    def _bomb(*a, **k):
        raise AssertionError("the default path must not call the Max-plan CLI")

    monkeypatch.setattr(maxplan, "run_claude", _bomb, raising=False)
    monkeypatch.setattr(maxplan, "cli_available", _bomb, raising=False)
    qa_path = driver.run_candidate_review(
        "riverbed", root=tmp_path, state_root=state_root, certified_at=_AS_OF)
    assert qa_path.is_file()


def test_composition_is_byte_deterministic(tmp_path, monkeypatch):
    state_root = _make_generation(tmp_path, monkeypatch)
    driver.run_candidate_review("riverbed", root=tmp_path, state_root=state_root, certified_at=_AS_OF)
    doc_json = state_root / "riverbed" / "riverbed.candidate_review.document.json"
    first = doc_json.read_text(encoding="utf-8")
    driver.run_candidate_review("riverbed", root=tmp_path, state_root=state_root, certified_at=_AS_OF)
    second = doc_json.read_text(encoding="utf-8")
    assert first == second


def test_author_seeds_flag_fails_named_until_pass_d(tmp_path, monkeypatch, capsys):
    _make_generation(tmp_path, monkeypatch)
    state_root = tmp_path / "data" / "state" / "candidate_review_v1"
    with pytest.raises(SystemExit):
        driver.main(["--client", "riverbed", "--state-root", str(state_root), "--author-seeds"])


def test_with_watch_runs_collection_and_press_in_one_process(tmp_path, monkeypatch):
    # 2026-07-24: the Command Center candidate_review step is ONE subprocess;
    # with_watch refreshes the generation first so the same-run as_of keeps
    # the 24h current-notice window open.
    registry = _wiring()._approved_world(tmp_path, monkeypatch)
    state_root = tmp_path / "data" / "state" / "candidate_review_v1"
    import run_candidate_review_watch as watch_mod
    real = watch_mod.run_watch_generation

    def _bound(client, **kwargs):
        kwargs.setdefault("registry_path", registry)
        return real(client, **kwargs)

    monkeypatch.setattr(watch_mod, "run_watch_generation", _bound)
    qa_path = driver.run_candidate_review(
        "riverbed", root=tmp_path, state_root=state_root,
        certified_at=_AS_OF, with_watch=True)
    assert qa_path.is_file()
    assert (state_root / "riverbed"
            / "riverbed.candidate_review.DRAFT.html").is_file()


def test_step_cmd_maps_candidate_review_to_the_one_process_driver():
    flask = pytest.importorskip("flask")
    _ = flask
    import ui.server as srv
    cmd = srv._step_cmd("candidate_review", "Riverbed", {})
    assert cmd[1:] == ["run_candidate_review.py", "--client", "Riverbed",
                       "--with-watch", "--live"]


def test_with_watch_mints_the_relevance_receipt_when_missing(
        tmp_path, monkeypatch):
    # 2026-07-25 self-sufficient press: the watch hard-requires a current
    # relevance receipt; with_watch mints it through the real deterministic
    # calibrator instead of failing named until a legacy refresh runs.
    import agents.assessment_chain as assessment_chain

    real_valid = assessment_chain.relevance_receipt_valid
    registry = _wiring()._approved_world(tmp_path, monkeypatch)
    monkeypatch.setattr(
        assessment_chain, "relevance_receipt_valid", real_valid)
    state_root = tmp_path / "data" / "state" / "candidate_review_v1"
    import run_candidate_review_watch as watch_mod
    real_watch = watch_mod.run_watch_generation

    def _bound(client, **kwargs):
        kwargs.setdefault("registry_path", registry)
        return real_watch(client, **kwargs)

    monkeypatch.setattr(watch_mod, "run_watch_generation", _bound)
    qa_path = driver.run_candidate_review(
        "riverbed", root=tmp_path, state_root=state_root,
        certified_at=_AS_OF, with_watch=True)
    assert qa_path.is_file()
    receipt = tmp_path / "data" / "state" / "relevance" / "riverbed.run.json"
    assert receipt.is_file()


def test_stale_sweep_outside_the_repo_fails_named_before_any_network(
        tmp_path, monkeypatch):
    # 2026-07-25: the press refreshes a stale sweep only at the repo root;
    # an isolated world with a stale sweep stops with a named reason and
    # never spawns the search subprocess.
    import json as _json

    registry = _wiring()._approved_world(tmp_path, monkeypatch)
    _ = registry
    packet_path = tmp_path / "data" / "review" / "riverbed.review.json"
    packet = _json.loads(packet_path.read_text(encoding="utf-8"))
    packet["decided_at"] = "2099-01-01T00:00:00+00:00"
    packet_path.write_text(_json.dumps(packet), encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        driver._ensure_current_sweep(tmp_path, "riverbed")
    assert "repo root" in str(caught.value)


def test_office_scale_agency_yield_is_never_capped_below_the_identity_bound():
    # 2026-08-04 operator rule: a relevant lead is never dropped by an
    # arbitrary frame cap. The Varonis all-federal sweep yielded 146
    # distinct office-level accounts and the old 128 bound refused the
    # whole press. The bound is a sanity rail against malformed sweeps
    # (now 1024, matching the SAM identity bound), not a filter.
    import run_candidate_review_watch as watch_mod

    rows = [
        {
            "source": "sam.gov",
            "source_id": f"NOTICE-{index:04d}",
            "agency": f"dept of example.office {index:04d}",
        }
        for index in range(200)
    ]
    frame = watch_mod._extract_dynamic_inputs(
        {"sam.gov": rows},
        focus_agencies=(),
        profile_competitors=("Rival A",),
    )
    assert len(frame["candidate_accounts"]) == 200
    assert len(frame["candidate_source_identities"]) == 200


def test_the_account_sanity_rail_still_fails_named_past_the_bound():
    import run_candidate_review_watch as watch_mod

    with pytest.raises(ValueError) as caught:
        watch_mod._ordered_unique(
            (f"office {index}" for index in range(1025)),
            maximum=1024,
        )
    assert "maximum is 1024" in str(caught.value)
