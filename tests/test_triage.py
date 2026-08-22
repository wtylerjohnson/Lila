"""Claude triage: batching, failure isolation, server join. No network."""
import json
from unittest import mock

from agents.decisions.triage import TriageItem, TriageResult, triage_notices


def _notices(n):
    return [{"source_id": f"N{i}", "title": f"Notice {i}", "agency": "DISA",
             "notice_type": "Solicitation", "naics_code": "541512",
             "response_deadline": "2026-08-01"} for i in range(n)]


def test_triage_batches_and_maps_verdicts():
    engine = mock.Mock()
    def fake_deliberate(*, layer, system_prompt, context, schema):
        items = [TriageItem(id=n["id"],
                            verdict="pursue" if n["id"] == "N0" else "discard",
                            reason="r") for n in context["notices"]]
        return TriageResult(items=items)
    engine.deliberate.side_effect = fake_deliberate
    out = triage_notices("Testco", "sells CTI", _notices(90), engine=engine, batch_size=40)
    assert engine.deliberate.call_count == 3          # 40+40+10
    assert out["N0"]["verdict"] == "pursue"
    assert out["N50"]["verdict"] == "discard"
    assert len(out) == 90


def test_triage_reads_notice_type_from_canonical_raw_payload():
    """The production RawOpportunity shape keeps SAM type in raw_payload."""
    engine = mock.Mock()
    seen = {}

    def capture(*, layer, system_prompt, context, schema):
        seen.update(context["notices"][0])
        return TriageResult(items=[
            TriageItem(id="N1", verdict="monitor", reason="early notice")])

    engine.deliberate.side_effect = capture
    out = triage_notices("Testco", "sells CTI", [{
        "source_id": "N1", "title": "Market research", "agency": "DHS",
        "naics_code": "541512",
        "raw_payload": {
            "type": "Sources Sought",
            "text": "Official PWS says threat intelligence platform.",
            "attachment_relevance_excerpt": (
                "Official PWS says threat intelligence platform."),
            "attachment_evidence": [{"resource_id": "a" * 32}],
            "attachment_evidence_sha256": "inventory-hash",
            "description_snippet": "Public notice opening language.",
            "screen_evidence_matches": [{
                "term": "threat intelligence",
                "field": "description",
                "matched_text": "threat intelligence",
                "context": "The requirement includes threat intelligence.",
            }],
        },
    }], engine=engine)
    assert seen["type"] == "Sources Sought"
    assert "Official PWS" in seen["official_attachment_excerpt"]
    assert seen["official_source_evidence"] == [
        {
            "field": "description_snippet",
            "context": "Public notice opening language.",
        },
        {
            "field": "description",
            "term": "threat intelligence",
            "matched_text": "threat intelligence",
            "context": "The requirement includes threat intelligence.",
        },
    ]
    assert out["N1"]["attachment_evidence_sha256"] == "inventory-hash"


def test_failed_batch_is_isolated_not_fatal():
    engine = mock.Mock()
    calls = {"n": 0}
    def flaky(*, layer, system_prompt, context, schema):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("model exploded")
        return TriageResult(items=[TriageItem(id=n["id"], verdict="monitor", reason="r")
                                   for n in context["notices"]])
    engine.deliberate.side_effect = flaky
    out = triage_notices("Testco", "s", _notices(60), engine=engine, batch_size=40)
    assert out["N0"]["verdict"] == "unscreened" and "exploded" in out["N0"]["reason"]
    assert out["N45"]["verdict"] == "monitor"


def _evidence_rich_notices(n):
    rows = _notices(n)
    for row in rows:
        row["raw_payload"] = {
            "description_snippet": "network evidence " * 45,
            "screen_evidence_matches": [{
                "field": "description",
                "term": "network switching",
                "matched_text": "network switching",
                "context": "official network switching requirement " * 16,
            } for _ in range(4)],
        }
    return rows


def test_evidence_rich_notices_batch_by_serialized_character_budget():
    engine = mock.Mock()

    def screen(*, layer, system_prompt, context, schema):
        return TriageResult(items=[
            TriageItem(id=n["id"], verdict="monitor", reason="evidence fit")
            for n in context["notices"]
        ])

    engine.deliberate.side_effect = screen
    out = triage_notices(
        "Testco",
        "sells network infrastructure",
        _evidence_rich_notices(9),
        engine=engine,
        batch_size=40,
        batch_char_budget=7_500,
    )

    assert engine.deliberate.call_count > 1
    assert list(out) == [f"N{i}" for i in range(9)]
    assert all(row["verdict"] == "monitor" for row in out.values())
    for call in engine.deliberate.call_args_list:
        context = call.kwargs["context"]
        serialized = json.dumps(
            context, indent=2, default=str, sort_keys=True)
        assert len(serialized) <= 7_500 or len(context["notices"]) == 1


def test_character_batched_failure_is_isolated_and_output_is_complete():
    engine = mock.Mock()
    failed_ids = []

    def flaky(*, layer, system_prompt, context, schema):
        if not failed_ids:
            failed_ids.extend(n["id"] for n in context["notices"])
            raise TimeoutError("triage timed out")
        return TriageResult(items=[
            TriageItem(id=n["id"], verdict="monitor", reason="recovered")
            for n in context["notices"]
        ])

    engine.deliberate.side_effect = flaky
    out = triage_notices(
        "Testco",
        "sells network infrastructure",
        _evidence_rich_notices(8),
        engine=engine,
        batch_size=40,
        batch_char_budget=7_500,
    )

    assert set(out) == {f"N{i}" for i in range(8)}
    assert all(out[source_id]["verdict"] == "unscreened"
               for source_id in failed_ids)
    assert all(out[source_id]["verdict"] == "monitor"
               for source_id in set(out) - set(failed_ids))
    assert engine.deliberate.call_count > 1


def test_server_joins_verdicts_and_ranks(tmp_path, monkeypatch):
    import json
    import ui.server as srv
    artifact = {"results": {"sam.gov": [
        {"source_id": "A", "title": "CTI platform", "response_deadline": "2026-09-01"},
        {"source_id": "B", "title": "Janitorial", "response_deadline": "2026-07-05"},
        {"source_id": "C", "title": "Maybe", "response_deadline": "2026-07-10"},
    ], "triage": {
        "A": {"verdict": "pursue", "reason": "core product"},
        "B": {"verdict": "discard", "reason": "wrong lane"},
        "C": {"verdict": "monitor", "reason": "adjacent"},
    }}}
    (tmp_path / "searches_x.json").write_text(json.dumps(artifact))
    monkeypatch.setattr(srv, "CLEANED_DIR", str(tmp_path))
    opps = srv._search_opportunities("x")
    assert [o["id"] for o in opps] == ["A", "C", "B"]  # pursue > monitor > discard
    assert opps[0]["reason"] == "core product"


def test_operator_guidance_threads_into_the_screen_context():
    """The review-opportunities loop: operator guidance re-screens the notices
    on disk. It reaches the model context with a never-invent instruction, and
    is absent when no guidance is given."""
    engine = mock.Mock()
    seen = {}

    def capture(*, layer, system_prompt, context, schema):
        seen.update(context)
        return TriageResult(items=[TriageItem(id=n["id"], verdict="monitor", reason="r")
                                   for n in context["notices"]])
    engine.deliberate.side_effect = capture

    triage_notices("Testco", "sells CTI", _notices(2), engine=engine,
                   directive="treat aviation-ops buyers more favorably")
    assert seen["operator_review_guidance"] == "treat aviation-ops buyers more favorably"
    assert "NEVER manufacture a fit" in seen["instruction"]

    seen.clear()
    triage_notices("Testco", "sells CTI", _notices(2), engine=engine)
    assert "operator_review_guidance" not in seen   # absent without guidance
    seen.clear()
    triage_notices("Testco", "s", _notices(2), engine=engine, directive="   ")
    assert "operator_review_guidance" not in seen   # blank guidance is a no-op
