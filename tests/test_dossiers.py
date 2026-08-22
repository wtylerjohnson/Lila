"""Deadline-aware depth: notice deep-fetch + pursuit dossiers."""

import json

import tools.api.sam_notice_detail as nd
import tools.api.sam_quota as sq
from agents.decisions.dossier import (
    PursuitDossier, compose_dossier, render_dossier_markdown, select_targets,
)

NOTICES = [
    {"source_id": "N-LATE", "title": "Late", "response_deadline": "2026-10-01"},
    {"source_id": "N-SOON", "title": "Soon", "response_deadline": "2026-07-20"},
    {"source_id": "N-OPEN", "title": "RFI no deadline", "response_deadline": None},
    {"source_id": "N-DISC", "title": "Discarded", "response_deadline": "2026-07-10"},
    {"source_id": "N-MON", "title": "Monitored", "response_deadline": "2026-07-11"},
]
VERDICTS = {
    "N-LATE": {"verdict": "pursue"}, "N-SOON": {"verdict": "pursue"},
    "N-OPEN": {"verdict": "pursue"}, "N-DISC": {"verdict": "discard"},
    "N-MON": {"verdict": "monitor"},
}


def test_select_targets_pursue_only_deadline_first_budgeted():
    t = select_targets(NOTICES, VERDICTS, budget=2)
    assert [n["source_id"] for n in t] == ["N-SOON", "N-LATE"]  # urgent first, RFI last
    t3 = select_targets(NOTICES, VERDICTS, budget=9)
    assert [n["source_id"] for n in t3] == ["N-SOON", "N-LATE", "N-OPEN"]
    assert select_targets(NOTICES, {}, budget=5) == []


def test_strip_html():
    txt = nd.strip_html("<p>Scope of <b>work</b>:</p><br><script>x()</script>threat&nbsp;intel")
    assert "Scope of work" in txt and "threat intel" in txt and "script" not in txt


def test_depth_fetch_meters_caches_and_never_caches_failure(monkeypatch):
    calls = []

    def fake_get(url, *, params=None, timeout=None, headers=None, retries=3):
        calls.append(url)
        if "noticedesc" in url:
            return {"description": "<p>Full scope text</p>"}
        return {"_embedded": [{"name": "SOW.pdf", "mimeType": "application/pdf"}]}

    monkeypatch.setattr(nd, "get_json", fake_get)
    before = sq.calls_today()
    d = nd.fetch_notice_depth("N-1", api_key="k")
    assert d["description"] == "Full scope text"
    assert d["attachments"][0]["name"] == "SOW.pdf"
    assert d["description_checked"] is True
    assert d["resources_checked"] is True
    assert d["retrieved_at"]
    assert sq.calls_today() == before + 1
    # Second call: served from permanent cache — no HTTP, no quota.
    calls.clear()
    d2 = nd.fetch_notice_depth("N-1", api_key="k")
    assert d2["from_cache"] is True and not calls and sq.calls_today() == before + 1

    # Failures are not cached: next attempt retries live.
    def boom(url, **kw):
        raise RuntimeError("429")

    monkeypatch.setattr(nd, "get_json", boom)
    f1 = nd.fetch_notice_depth("N-2", api_key="k")
    assert f1["description"] is None and f1["errors"]
    monkeypatch.setattr(nd, "get_json", fake_get)
    f2 = nd.fetch_notice_depth("N-2", api_key="k")
    assert f2["description"] == "Full scope text" and f2["from_cache"] is False


def test_cached_description_retries_keyless_attachment_failure(monkeypatch):
    calls = []

    def first(url, *, params=None, timeout=None, headers=None, retries=3):
        calls.append(url)
        if "noticedesc" in url:
            return {"description": "Authoritative requirement"}
        raise RuntimeError("resources temporarily unavailable")

    monkeypatch.setattr(nd, "get_json", first)
    d1 = nd.fetch_notice_depth("N-RESOURCE-RETRY", api_key="k")
    assert d1["description"] == "Authoritative requirement"
    assert d1["resources_checked"] is False

    calls.clear()

    def retry(url, *, params=None, timeout=None, headers=None, retries=3):
        calls.append(url)
        assert "resources" in url
        return {"_embedded": [{"name": "PWS.pdf", "mimeType": "application/pdf"}]}

    monkeypatch.setattr(nd, "get_json", retry)
    d2 = nd.fetch_notice_depth("N-RESOURCE-RETRY", api_key="k")
    assert d2["from_cache"] is True
    assert d2["resources_checked"] is True
    assert d2["attachments"][0]["name"] == "PWS.pdf"
    assert len(calls) == 1


def test_cache_refreshes_when_notice_modification_postdates_depth(monkeypatch):
    descriptions = iter(["Original requirement", "Amended requirement"])

    def fetched(url, *, params=None, timeout=None, headers=None, retries=3):
        if "noticedesc" in url:
            return {"description": next(descriptions)}
        return []

    monkeypatch.setattr(nd, "get_json", fetched)
    first = nd.fetch_notice_depth("N-AMENDED", api_key="k")
    assert first["description"] == "Original requirement"
    cache = nd._cache_dir() / "N-AMENDED.json"
    stored = json.loads(cache.read_text())
    stored["retrieved_at"] = "2020-01-01T00:00:00+00:00"
    cache.write_text(json.dumps(stored))

    refreshed = nd.fetch_notice_depth(
        "N-AMENDED", api_key="k",
        min_retrieved_at="2026-07-10T00:00:00+00:00")
    assert refreshed["description"] == "Amended requirement"
    assert refreshed["description_checked"] is True
    assert not any("predates current notice" in error
                   for error in refreshed["errors"])
    assert list((nd._cache_dir() / ".history" / "N-AMENDED.json").glob("*.json"))


def test_unknown_resources_schema_never_confirms_zero_attachments(monkeypatch):
    def fetched(url, *, params=None, timeout=None, headers=None, retries=3):
        if "noticedesc" in url:
            return {"description": "Authoritative requirement"}
        return {"unexpected": {"rows": []}}

    monkeypatch.setattr(nd, "get_json", fetched)
    depth = nd.fetch_notice_depth("N-SCHEMA-DRIFT", api_key="k")
    assert depth["resources_checked"] is False
    assert depth["resources_schema"] is None
    assert depth["attachments"] is None
    assert any("unrecognized resources schema" in error
               for error in depth["errors"])


def _dossier(**over):
    base = dict(
        id="N-SOON", title="CTI Platform BPA",
        scope_summary="DHS seeks an enterprise threat intelligence platform.",
        evaluation_criteria=["Technical approach", "Past performance"],
        submission_requirements=["10-page limit", "Submit via sam.gov by 07/20"],
        incumbent_signals=["References BigCo's existing license"],
        vehicle="GSA MAS",
        fit_verdict="strong_fit",
        win_themes=["Dark-web coverage depth", "Existing DHS footprint"],
        red_flags=["Brand-name-or-equal language"],
        next_action="Request the Q&A attachment before 07/10.",
    )
    base.update(over)
    return PursuitDossier(**base)


class FakeEngine:
    def deliberate(self, layer, system_prompt, context, schema):
        self.ctx = context
        return _dossier()


def test_compose_passes_depth_text_and_render_covers_sections():
    eng = FakeEngine()
    d = compose_dossier("Recorded Future", "sell CTI", NOTICES[1],
                        {"description": "x" * 20000, "attachments": [], "errors": []},
                        engine=eng)
    assert len(eng.ctx["full_description"]) == 14000  # prompt-size cap holds
    md = render_dossier_markdown(d, NOTICES[1])
    for needle in ("CTI Platform BPA", "STRONG FIT", "due 2026-07-20",
                   "Evaluation criteria", "Submission requirements",
                   "Incumbent signals", "Win themes", "Red flags",
                   "Next action", "GSA MAS"):
        assert needle in md, needle
