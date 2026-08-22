"""Direct-press coverage for the machine client-relevance contract."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


def _press_with_stubbed_machine_pair(
        tmp_path, monkeypatch, *, relevance_validator,
        executive_validator, sweep_owner="Testco", calendar=None) -> int:
    import run_signal_board
    from agents import assessment_chain, review
    from agents.reports import (
        board_content,
        composer,
        signal_board_presentation,
    )
    from tools import capability
    from tools.api import recompete

    sweep_path = tmp_path / "searches_testco.json"
    sweep_path.write_text(json.dumps({
        "client": sweep_owner,
        "results": {},
    }), encoding="utf-8")
    content = SimpleNamespace(
        client_name="Testco",
        composition_mode="machine",
        figures=[],
    )
    profile = SimpleNamespace(
        client_name="Testco",
        is_populated=lambda: True,
    )

    monkeypatch.setattr(run_signal_board, "ROOT", str(tmp_path))
    monkeypatch.setattr(
        run_signal_board, "REPORT_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(
        signal_board_presentation, "presentation_digest",
        lambda _client, *, root: "0" * 64)
    monkeypatch.setattr(
        review, "sweep_artifact_path", lambda _client: str(sweep_path))
    monkeypatch.setattr(
        board_content, "load_board_content", lambda _client: content)
    monkeypatch.setattr(capability, "load_profile", lambda _client: profile)
    monkeypatch.setattr(recompete, "load_calendar", lambda _client: calendar)
    taxonomy = object()
    monkeypatch.setattr(
        composer, "load_client_inputs",
        lambda _client: {"taxonomy": taxonomy, "scope": None})
    monkeypatch.setattr(
        assessment_chain, "validate_current_compose_pair",
        lambda *_args, **_kwargs: (content, "bound compose trail"))
    monkeypatch.setattr(
        composer, "validate_machine_teaming_contract",
        lambda _content: None)
    monkeypatch.setattr(
        composer, "validate_machine_horizon_contract",
        lambda _content, *, trail_md: None)
    monkeypatch.setattr(
        composer, "validate_machine_client_relevance_contract",
        relevance_validator, raising=False)
    monkeypatch.setattr(
        composer, "validate_machine_executive_summary_contract",
        executive_validator)

    return run_signal_board._press(SimpleNamespace(
        client="Testco",
        render_date="2026-07-19",
        replay=True,
        out=None,
        offline=True,
    ))


def test_press_refuses_named_when_client_relevance_evidence_fails(
        tmp_path, monkeypatch, capsys):
    seen: list[tuple[object, str]] = []

    def reject(content, *, trail_md, taxonomy, profile, sweep, calendar):
        seen.append((content, trail_md))
        assert taxonomy is not None
        assert profile.client_name == "Testco"
        assert sweep == {"client": "Testco", "results": {}}
        assert calendar is None
        raise ValueError("trail-bound relevance basis does not match")

    def executive_must_not_run(_content, *, sweep):
        raise AssertionError("executive-summary gate ran after refusal")

    rc = _press_with_stubbed_machine_pair(
        tmp_path,
        monkeypatch,
        relevance_validator=reject,
        executive_validator=executive_must_not_run,
    )

    assert rc == 2
    assert len(seen) == 1
    assert seen[0][1] == "bound compose trail"
    err = capsys.readouterr().err
    assert "REFUSED: machine client relevance evidence failed" in err
    assert "trail-bound relevance basis does not match" in err


def test_press_accepts_client_relevance_evidence_and_continues(
        tmp_path, monkeypatch, capsys):
    seen: list[str] = []

    def accept(
            _content, *, trail_md, taxonomy, profile, sweep, calendar):
        assert taxonomy is not None
        assert profile.client_name == "Testco"
        assert sweep == {"client": "Testco", "results": {}}
        assert calendar is None
        seen.append(trail_md)

    def stop_at_next_gate(_content, *, sweep):
        raise ValueError("next gate reached")

    rc = _press_with_stubbed_machine_pair(
        tmp_path,
        monkeypatch,
        relevance_validator=accept,
        executive_validator=stop_at_next_gate,
    )

    assert rc == 2
    assert seen == ["bound compose trail"]
    err = capsys.readouterr().err
    assert "machine client relevance evidence failed" not in err
    assert "REFUSED: machine executive summary evidence failed" in err
    assert "next gate reached" in err


@pytest.mark.parametrize(
    ("sweep_owner", "calendar", "refusal"),
    [
        ("Other Client", None, "stored sweep belongs to 'Other Client'"),
        (
            "Testco",
            {"client": "Other Client", "attack": []},
            "recompete calendar belongs to 'Other Client'",
        ),
    ],
)
def test_press_refuses_foreign_current_source_owner(
        tmp_path, monkeypatch, capsys, sweep_owner, calendar, refusal):
    def must_not_validate(*_args, **_kwargs):
        raise AssertionError("semantic gates ran after foreign source refusal")

    rc = _press_with_stubbed_machine_pair(
        tmp_path,
        monkeypatch,
        relevance_validator=must_not_validate,
        executive_validator=must_not_validate,
        sweep_owner=sweep_owner,
        calendar=calendar,
    )

    assert rc == 2
    err = capsys.readouterr().err
    assert refusal in err
    assert "path placement is not identity proof" in err
